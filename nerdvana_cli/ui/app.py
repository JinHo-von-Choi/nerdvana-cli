"""NerdVana TUI -- Textual-based terminal interface."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import threading
import time
from collections.abc import Callable
from functools import partial
from typing import Any

from rich.markup import escape
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import DirectoryTree, Footer, Header, Input, OptionList, Static

from nerdvana_cli.cli.bootstrap import loop_factories
from nerdvana_cli.core.activity_state import ActivityState
from nerdvana_cli.core.agent_loop import AgentLoop
from nerdvana_cli.core.session import SessionStorage, resume_session_id
from nerdvana_cli.core.settings import NerdvanaSettings
from nerdvana_cli.core.skills import Skill
from nerdvana_cli.core.task_state import TaskRegistry, TaskState
from nerdvana_cli.core.user_commands import UserCommand, UserCommandLoader
from nerdvana_cli.tools.registry import create_tool_registry
from nerdvana_cli.ui.banner import build_banner
from nerdvana_cli.ui.dashboard_tab import DashboardTab
from nerdvana_cli.ui.editor_controller import EditorBufferController
from nerdvana_cli.ui.editor_pane import EditorPane
from nerdvana_cli.ui.live_panels import (
    refresh_mcp_section,
    refresh_sidebar_tasks,
    schedule_sidebar_file_refresh,
    update_context_usage,
)
from nerdvana_cli.ui.menu_controller import handle_option_selected, refresh_command_menu, seed_skill_options
from nerdvana_cli.ui.project_tree import ProjectTreePane
from nerdvana_cli.ui.sidebar import Sidebar
from nerdvana_cli.ui.update_notice import check_for_update
from nerdvana_cli.ui.widgets import (
    ActivityIndicator,
    AskUserScreen,
    ChatMessage,
    CommandMenu,
    ConfirmScreen,
    ModelSelector,
    MultilineAwareInput,
    ProviderSelector,
    StatusBar,
    StreamingOutput,
    ToolStatusLine,
)

logger = logging.getLogger(__name__)


def make_activity_change_callback(
    app:           Any,
    ui_thread_id:  int,
) -> Callable[[ActivityState], None]:
    """Build the callback AgentLoop uses to publish activity-state changes.

    The agent loop runs as a Textual async worker, so it shares the thread that
    owns the event loop. ``call_from_thread`` refuses to run there and raises,
    which is why the indicator stayed frozen. The caller's thread is compared
    against *ui_thread_id* and the widget is written directly when they match.

    A failure here is logged rather than raised: the loop's activity dispatch
    swallows exceptions, so an unlogged one would be undiagnosable.
    """

    def on_activity_change(state: ActivityState) -> None:
        def apply() -> None:
            app.query_one("#activity-indicator", ActivityIndicator).state = state

        try:
            if threading.get_ident() == ui_thread_id:
                apply()
            else:
                app.call_from_thread(apply)
        except Exception as exc:  # noqa: BLE001
            logger.warning("activity indicator update failed: %s", exc)

    return on_activity_change


class NerdvanaApp(App[object]):
    """Main TUI application."""

    TITLE = "NerdVana CLI"
    CSS_PATH = "styles.tcss"

    _SIDEBAR_BREAKPOINT = 140

    DEFAULT_CSS = """
    Screen {
        background: #1a1a2e;
    }
    Header {
        background: #16213e;
        color: #a8b2d1;
    }
    Footer {
        background: #16213e;
        color: #64748b;
    }
    Footer > .footer--key {
        background: #0f3460;
        color: #e2e8f0;
    }
    Footer > .footer--description {
        color: #94a3b8;
    }
    #body {
        height: 1fr;
    }
    #main-container {
        height: 1fr;
    }
    #logo-banner {
        height: auto;
        padding: 1 0 0 0;
        content-align: center middle;
        text-align: center;
    }
    #chat-frame {
        height: 1fr;
        border: solid #334155;
        padding: 0 1;
    }
    #user-input {
        dock: bottom;
        margin: 0 0;
        border: tall #334155;
        background: #1a1a2e;
        color: #e2e8f0;
    }
    #user-input:focus {
        border: tall #7c3aed;
    }
    #context-bar {
        dock: bottom;
        height: 1;
        background: #16213e;
        color: #64748b;
        padding: 0 1;
    }
    #status-bar {
        dock: bottom;
        height: 1;
        background: #16213e;
        color: #64748b;
        padding: 0 1;
    }
    """

    BINDINGS = [
        Binding("ctrl+c", "quit",             "Quit",      show=True),
        Binding("ctrl+b", "toggle_sidebar",   "Sidebar",   show=True),
        Binding("ctrl+e", "toggle_project_tree", "Files",  show=True,  priority=True),
        Binding("ctrl+o", "toggle_editor",    "Editor",    show=True,  priority=True),
        Binding("ctrl+s", "save_editor",      "Save",      show=True,  priority=True),
        Binding("ctrl+l", "clear_chat",       "Clear",     show=True),
        Binding("ctrl+d", "toggle_dashboard", "Dashboard", show=True),
        Binding("escape", "focus_input",      "Input",     show=False, priority=True),
    ]

    def __init__(
        self,
        settings: NerdvanaSettings,
        parism_client: Any = None,
        mcp_manager: Any = None,
        resume_id: str | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.settings       = settings
        self.parism_client  = parism_client
        self.mcp_manager    = mcp_manager
        self._resume_id     = resume_id
        self._agent_loop: AgentLoop | None = None
        self._is_generating = False
        self._confirm_lock  = asyncio.Lock()
        self._pending_images: list[dict[str, Any]] | None = None
        self._commands_cache:   list[UserCommand] = []
        self._commands_scanned: float             = float("-inf")
        self._pending_provider: str = ""  # provider name awaiting API key input
        self._task_registry = TaskRegistry()
        self._sidebar_user_visible: bool | None = None  # None = follow auto rule
        self._session_topic: str        = ""
        self._project_root: str         = os.getcwd()
        self._editor                    = EditorBufferController(self)

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="body"):
            yield Sidebar(id="sidebar")
            yield ProjectTreePane(root_path=self._project_root, id="project-tree-pane")
            with Vertical(id="main-container"):
                yield Static(id="logo-banner")
                with VerticalScroll(id="chat-frame"):
                    yield StreamingOutput(id="streaming-output")
                    yield ToolStatusLine(id="tool-status")
                yield DashboardTab(id="dashboard-tab")
                yield ActivityIndicator(id="activity-indicator")
                yield CommandMenu(id="command-menu")
                yield ProviderSelector(id="provider-selector")
                yield ModelSelector(id="model-selector")
                yield MultilineAwareInput(
                    placeholder="Message...",
                    id="user-input",
                )
            yield EditorPane(project_root=self._project_root, id="editor-pane")
        yield Static(id="context-bar")
        yield StatusBar(id="status-bar")
        yield Footer()

    async def _ask_user_prompt(self, question: str, options: list[str]) -> str | None:
        """Show the AskUser modal and wait for the answer.

        Registered as the agent loop's ``on_ask_user`` hook. The loop runs as a
        Textual worker on the app's event loop, so the modal is pushed directly
        and its dismissal resolves a future the tool call awaits. Returns None
        when the user dismisses the prompt.
        """
        answer: asyncio.Future[str | None] = asyncio.get_running_loop().create_future()

        def _on_dismiss(result: str | None) -> None:
            if not answer.done():
                answer.set_result(result)

        self.push_screen(AskUserScreen(question, options), _on_dismiss)
        return await answer

    def take_pending_images(self) -> list[dict[str, Any]] | None:
        """The images attached by ``/image`` for the next prompt, handed over once."""
        images, self._pending_images = self._pending_images, None
        return images

    async def _confirm_prompt(self, tool_name: str, message: str) -> bool:
        """Ask the user to allow a tool call through a modal.

        Registered as the agent loop's ``on_confirm`` hook. Requests are served
        one at a time, so a sub-agent asking while another prompt is open waits
        its turn instead of stacking dialogs.
        """
        async with self._confirm_lock:
            decision: asyncio.Future[bool] = asyncio.get_running_loop().create_future()

            def _on_dismiss(result: bool | None) -> None:
                if not decision.done():
                    decision.set_result(bool(result))

            self.push_screen(ConfirmScreen(tool_name, message), _on_dismiss)
            return await decision

    def _on_background_task_finished(self, task: TaskState) -> None:
        """Defer the wake-up so it runs after the finishing task returns."""
        self.call_later(self._wake_for_background)

    def _wake_for_background(self) -> None:
        """Start a turn to report finished background work when the agent is idle.

        While a turn is running, the agent loop reports finished tasks at its
        next step, and the response runner calls this again once it is done.
        """
        if self._is_generating or self._agent_loop is None or not self._task_registry.has_unreported():
            return
        self._add_chat_message("[dim]Background work finished; reviewing the result.[/dim]")
        self._generate_response("Background work finished. Review the reported results and continue.")

    def on_unmount(self) -> None:
        """Close the agent session so SESSION_END hooks run on exit."""
        if self._agent_loop is not None:
            self._agent_loop.close_session("exit")

    def on_mount(self) -> None:
        """Initialize agent loop and display welcome."""
        registry = create_tool_registry(
            parism_client = self.parism_client,
            mcp_tools     = self.mcp_manager.get_all_tools() if self.mcp_manager else [],
            settings      = self.settings,
            task_registry = self._task_registry,
        )
        skills = self._start_agent_loop(registry).skill_loader.list_skills()
        self._update_banner()
        self._init_sidebar(registry, skills)
        seed_skill_options(self.query_one("#command-menu", CommandMenu), skills)

        status = self.query_one("#status-bar", StatusBar)
        status.update_status(
            model=self.settings.model.model,
            provider=self.settings.model.provider,
            tools=len(registry.all_tools()),
            parism=self.parism_client is not None,
        )

        # Session start context summary
        self._show_session_context(registry)

        self.query_one("#user-input", Input).focus()

        if not self.settings.session.show_activity:
            with contextlib.suppress(Exception):
                self.query_one("#activity-indicator", ActivityIndicator).styles.display = "none"

        self._show_load_warnings()
        self._check_update_task = asyncio.create_task(check_for_update(self))

    def _start_agent_loop(self, registry: Any) -> AgentLoop:
        """Create the agent loop on its session and restore the history of a resumed one."""
        resume_id = resume_session_id(self._resume_id)
        session   = SessionStorage(session_id=resume_id, persist=self.settings.session.persist)

        self._agent_loop = AgentLoop(
            settings           = self.settings,
            registry           = registry,
            session            = session,
            task_registry      = self._task_registry,
            on_activity_change = make_activity_change_callback(self, threading.get_ident()),
            on_ask_user        = self._ask_user_prompt,
            on_confirm         = self._confirm_prompt,
            factories          = loop_factories(),
        )
        if resume_id:
            restored = self._agent_loop.restore_history()
            self.notify(f"Resumed session {resume_id}: {restored} message(s) restored.")
        return self._agent_loop

    def _init_sidebar(self, registry: Any, skills: list[Skill]) -> None:
        """Fill the sidebar sections and start the timers that keep them current."""
        sidebar = self.query_one("#sidebar", Sidebar)
        sidebar.set_header(topic=self._session_topic, cwd=self._project_root)
        sidebar.set_context(
            provider=self.settings.model.provider,
            model=self.settings.model.model,
            pct=0,
        )
        sidebar.set_tools([t.name for t in registry.all_tools()])
        refresh_mcp_section(self)
        self.set_interval(2.0, partial(refresh_mcp_section, self))

        sidebar.set_skills([s.trigger for s in skills])
        sidebar.set_tasks_registry(self._task_registry)
        self._task_registry.add_listener(self._on_background_task_finished)
        self.set_interval(0.5, partial(refresh_sidebar_tasks, self))
        self.set_interval(2.0, partial(schedule_sidebar_file_refresh, self))

    def _show_load_warnings(self) -> None:
        """Show config problems recovered during loading, once at startup."""
        warnings = getattr(self.settings, "load_warnings", None)
        if not isinstance(warnings, list) or not warnings:
            return
        lines = "\n".join(f"  {w.format()}" for w in warnings)
        self._add_chat_message(
            f"[yellow]Config warnings (run `nerdvana doctor` for details):[/yellow]\n{escape(lines)}",
        )

    def on_resize(self, event: object) -> None:
        """Apply the 140-col breakpoint unless the user has explicitly toggled."""
        sidebar = self.query_one("#sidebar", Sidebar)
        if self._sidebar_user_visible is not None:
            return
        auto_show = self.size.width >= self._SIDEBAR_BREAKPOINT
        sidebar.set_class(not auto_show, "hidden")

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        """Handle user input submission."""
        input_widget = self.query_one("#user-input", MultilineAwareInput)

        if input_widget._pending_multiline is not None:
            user_text                       = input_widget._pending_multiline.strip()
            input_widget._pending_multiline = None
        else:
            user_text = event.value.strip()

        if not user_text:
            return

        input_widget.value = ""

        if not self._session_topic and not user_text.startswith("/"):
            self._session_topic = user_text
            self.query_one("#sidebar", Sidebar).set_header(
                topic=self._session_topic,
                cwd=self._project_root,
            )

        # API key input mode
        if self._pending_provider:
            await self._handle_api_key_input(user_text)
            return

        if user_text.startswith("/"):
            await self._handle_command(user_text)
            return

        if self._is_generating:
            if self._agent_loop is not None:
                self._agent_loop.queue_input(user_text)
                self._add_chat_message(
                    f"\n[bold green]> {escape(user_text)}[/bold green] [dim](queued, applied at the next step)[/dim]",
                    raw_text=user_text,
                )
            return

        self._add_chat_message(f"\n[bold green]> {user_text}[/bold green]", raw_text=user_text)
        self._add_chat_message("[bold cyan]Estelle :[/bold cyan]")

        self._generate_response(user_text)

    def _user_commands(self) -> list[UserCommand]:
        """The user's command templates, rescanned at most every two seconds."""
        now = time.monotonic()
        if now - self._commands_scanned > 2.0:
            self._commands_cache   = UserCommandLoader(project_dir=self.settings.cwd or ".").list_commands()
            self._commands_scanned = now
        return self._commands_cache

    def _start_prompt(self, shown: str, prompt: str) -> None:
        """Send *prompt* as if the user had typed *shown*, queueing it behind a running response."""
        if self._is_generating:
            if self._agent_loop is not None:
                self._agent_loop.queue_input(prompt)
                self._add_chat_message(
                    f"\n[bold green]> {escape(shown)}[/bold green] [dim](queued, applied at the next step)[/dim]",
                    raw_text=shown,
                )
            return
        self._add_chat_message(f"\n[bold green]> {escape(shown)}[/bold green]", raw_text=shown)
        self._add_chat_message("[bold cyan]Estelle :[/bold cyan]")
        self._generate_response(prompt)

    def _after_response(self) -> None:
        """Pick up what accumulated while a response was being generated."""
        self._drain_queued_input()
        self._wake_for_background()

    def _drain_queued_input(self) -> None:
        """Start a turn with text typed ahead that the finished run did not get to."""
        loop = self._agent_loop
        if self._is_generating or loop is None or not loop.has_queued_input():
            return
        self._add_chat_message("[bold cyan]Estelle :[/bold cyan]")
        self._generate_response("\n\n".join(loop.take_queued_input()))

    @work(exclusive=True)
    async def _generate_response(self, prompt: str) -> None:
        """Run agent loop and stream response to chat.

        Delegates the heavy lifting to ``ui.response_runner`` so the App class
        stays focused on widget composition and event wiring.
        """
        from nerdvana_cli.ui.response_runner import run_response_stream
        await run_response_stream(self, prompt)

    async def _handle_api_key_input(self, api_key: str) -> None:
        """Handle API key input for /provider flow."""
        from nerdvana_cli.commands.model_commands import handle_api_key_input
        await handle_api_key_input(self, api_key)

    def _show_session_context(self, registry: Any) -> None:
        """Show session startup context summary."""
        from nerdvana_cli.commands.session_commands import show_session_context
        show_session_context(self, registry)

    def _add_chat_message(self, markup: str, raw_text: str = "", thinking: str = "") -> None:
        """Add a clickable chat message to the chat frame."""
        chat_frame  = self.query_one("#chat-frame", VerticalScroll)
        streaming   = self.query_one("#streaming-output", StreamingOutput)
        full_markup = markup
        if thinking and getattr(self.settings.model, "show_thinking", True):
            thinking_block = (
                f"[dim italic][thinking]\n{thinking}\n[/thinking][/dim italic]\n\n"
            )
            full_markup = thinking_block + markup
        msg = ChatMessage(full_markup, raw_text=raw_text)
        chat_frame.mount(msg, before=streaming)
        chat_frame.scroll_end(animate=False)

    def _clear_chat_messages(self) -> None:
        """Remove all ChatMessage widgets from the chat frame."""
        chat_frame = self.query_one("#chat-frame", VerticalScroll)
        for msg in chat_frame.query(ChatMessage):
            msg.remove()

    def _update_context_usage(self, pct: int) -> None:
        """Update the context usage bar widget."""
        update_context_usage(self, pct)

    def _update_banner(self) -> None:
        """Update the logo banner with current provider/model info."""
        registry_count = len(self._agent_loop.registry.all_tools()) if self._agent_loop else 0
        self.query_one("#logo-banner", Static).update(
            build_banner(self.settings, registry_count, self.parism_client is not None)
        )

    async def _handle_command(self, cmd: str) -> None:
        """Handle slash commands by delegating to ``ui.command_dispatcher``."""
        from nerdvana_cli.ui.command_dispatcher import dispatch_command
        await dispatch_command(self, cmd)

    def on_input_changed(self, event: Input.Changed) -> None:
        """Show/hide command menu based on input."""
        # Discard multiline original when the user manually edits the summary.
        # Preserve it while the call_after_refresh callback has not yet run.
        input_widget = self.query_one("#user-input", MultilineAwareInput)
        if (
            input_widget._pending_multiline is not None
            and not event.value.startswith("[")
            and not input_widget._setting_summary
        ):
            input_widget._pending_multiline = None

        refresh_command_menu(self, event.value)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        """Handle command menu, model selector, or provider selector."""
        handle_option_selected(self, event)

    def action_clear_chat(self) -> None:
        """Clear chat action (Ctrl+L)."""
        asyncio.create_task(self._handle_command("/clear"))

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        """Let Escape reach an open modal instead of the app-wide focus binding."""
        if action == "focus_input" and isinstance(self.screen, ModalScreen):
            return False
        return super().check_action(action, parameters)

    def action_focus_input(self) -> None:
        """Focus input widget (Escape)."""
        self.query_one("#user-input", Input).focus()

    def action_toggle_project_tree(self) -> None:
        """Toggle project file tree visibility."""
        tree_pane      = self.query_one("#project-tree-pane", ProjectTreePane)
        became_visible = tree_pane.toggle_pane()
        if became_visible:
            tree_pane.focus_tree()

    def action_toggle_editor(self) -> None:
        """Toggle direct editor visibility."""
        editor         = self.query_one("#editor-pane", EditorPane)
        became_visible = editor.toggle_pane()
        if became_visible:
            editor.focus_editor()

    def action_save_editor(self) -> None:
        """Save the active editor buffer using the safe path helpers."""
        self._editor.save_active()

    def action_toggle_sidebar(self) -> None:
        """Toggle sidebar visibility. Sets a user-override that suppresses on_resize."""
        sidebar = self.query_one("#sidebar", Sidebar)
        currently_hidden = "hidden" in sidebar.classes
        self._sidebar_user_visible = currently_hidden
        sidebar.set_class(not currently_hidden, "hidden")

    def action_toggle_dashboard(self) -> None:
        """Toggle observability dashboard (Ctrl+D)."""
        with contextlib.suppress(Exception):
            self.query_one("#dashboard-tab", DashboardTab).toggle()

    def on_directory_tree_file_selected(self, event: DirectoryTree.FileSelected) -> None:
        """Open selected project file in the editor pane."""
        self._editor.open_selected(event.path)

    def _open_editor_buffer(self, relative_path: str) -> None:
        """Load a relative project path into the direct editor pane."""
        self._editor.open(relative_path)

    def _read_editor_buffer(self, relative_path: str) -> str:
        """Read a project file through validated, symlink-aware path handling."""
        return self._editor.read(relative_path)

    def _save_editor_buffer(self, relative_path: str, content: str) -> None:
        """Write a project file through validated, symlink-aware path handling."""
        self._editor.write(relative_path, content)
