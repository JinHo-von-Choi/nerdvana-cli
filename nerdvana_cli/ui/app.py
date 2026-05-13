"""NerdVana TUI -- Textual-based terminal interface."""

from __future__ import annotations

import asyncio
import contextlib
import os
from pathlib import Path
from typing import Any

from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import DirectoryTree, Footer, Header, Input, OptionList, Static
from textual.widgets.option_list import Option

from nerdvana_cli import __version__
from nerdvana_cli.core.activity_state import ActivityState
from nerdvana_cli.core.agent_loop import AgentLoop
from nerdvana_cli.core.session import SessionStorage
from nerdvana_cli.core.settings import NerdvanaSettings
from nerdvana_cli.core.task_state import TaskRegistry
from nerdvana_cli.tools.registry import create_tool_registry
from nerdvana_cli.ui.dashboard_tab import DashboardTab
from nerdvana_cli.ui.editor_pane import EditorPane, language_for_path
from nerdvana_cli.ui.project_tree import ProjectTreePane
from nerdvana_cli.ui.sidebar import Sidebar
from nerdvana_cli.ui.sidebar_sections import SidebarTasksSection
from nerdvana_cli.ui.widgets import (
    SLASH_COMMANDS,
    ActivityIndicator,
    ChatMessage,
    CommandMenu,
    ModelSelector,
    MultilineAwareInput,
    ProviderSelector,
    StatusBar,
    StreamingOutput,
    ToolStatusLine,
)
from nerdvana_cli.utils.path import safe_open_fd, validate_path

_MAX_EDITOR_FILE_BYTES = 1_000_000


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
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.settings       = settings
        self.parism_client  = parism_client
        self.mcp_manager    = mcp_manager
        self._agent_loop: AgentLoop | None = None
        self._is_generating = False
        self._pending_provider: str = ""  # provider name awaiting API key input
        self._task_registry = TaskRegistry()
        self._sidebar_user_visible: bool | None = None  # None = follow auto rule
        self._session_topic: str        = ""
        self._project_root: str         = os.getcwd()
        self._active_editor_path: str   = ""
        self._editor_dirty: bool        = False

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

    def on_mount(self) -> None:
        """Initialize agent loop and display welcome."""
        from nerdvana_cli.core.team import TeamRegistry

        mcp_tools     = self.mcp_manager.get_all_tools() if self.mcp_manager else []
        team_registry = TeamRegistry()
        registry      = create_tool_registry(
            parism_client = self.parism_client,
            mcp_tools     = mcp_tools,
            settings      = self.settings,
            task_registry = self._task_registry,
            team_registry = team_registry,
        )
        session  = SessionStorage()

        def _on_activity_change(state: ActivityState) -> None:
            self.call_from_thread(
                lambda: self.query_one(
                    "#activity-indicator", ActivityIndicator
                ).__setattr__("state", state)
            )

        self._agent_loop = AgentLoop(
            settings           = self.settings,
            registry           = registry,
            session            = session,
            task_registry      = self._task_registry,
            team_registry      = team_registry,
            on_activity_change = _on_activity_change,
        )

        self._update_banner()

        sidebar = self.query_one("#sidebar", Sidebar)
        sidebar.set_header(topic=self._session_topic, cwd=self._project_root)
        sidebar.set_context(
            provider=self.settings.model.provider,
            model=self.settings.model.model,
            pct=0,
        )
        sidebar.set_tools([t.name for t in registry.all_tools()])
        self._refresh_mcp_section()
        self.set_interval(2.0, self._refresh_mcp_section)

        triggers = [s.trigger for s in self._agent_loop.skill_loader.list_skills()]
        sidebar.set_skills(triggers)
        sidebar.set_tasks_registry(self._task_registry)
        self.set_interval(0.5, self._refresh_sidebar_tasks)
        self.set_interval(2.0, self._schedule_sidebar_file_refresh)

        menu = self.query_one("#command-menu", CommandMenu)
        # Pre-seed seen set with built-in slash command IDs to avoid DuplicateID
        _seen_triggers: set[str] = {cmd for cmd, _ in SLASH_COMMANDS}
        for skill in self._agent_loop.skill_loader.list_skills():
            if skill.trigger not in _seen_triggers:
                menu.add_option(Option(f"{skill.trigger}  {skill.description}", id=skill.trigger))
                _seen_triggers.add(skill.trigger)

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

        self._check_update_task = asyncio.create_task(self._check_update())

    def _refresh_mcp_section(self) -> None:
        """Update sidebar MCP section from mcp_manager.get_status()."""
        if not self.mcp_manager:
            return
        status_map = self.mcp_manager.get_status()
        servers: list[tuple[str, str]] = [
            (name, "connected" if ok else "error")
            for name, ok in status_map.items()
        ]
        with contextlib.suppress(Exception):
            self.query_one("#sidebar", Sidebar).set_mcp(servers)

    def _refresh_sidebar_tasks(self) -> None:
        """Refresh sidebar task rows when the widget is still mounted."""
        with contextlib.suppress(Exception):
            self.query_one("#sidebar-tasks", SidebarTasksSection).refresh_rows()

    def _schedule_sidebar_file_refresh(self) -> None:
        """Schedule async sidebar file refresh when the sidebar is still mounted."""
        with contextlib.suppress(Exception):
            sidebar = self.query_one("#sidebar", Sidebar)
            asyncio.create_task(sidebar.refresh_files())

    def on_resize(self, event: object) -> None:
        """Apply the 140-col breakpoint unless the user has explicitly toggled."""
        sidebar = self.query_one("#sidebar", Sidebar)
        if self._sidebar_user_visible is not None:
            return
        auto_show = self.size.width >= self._SIDEBAR_BREAKPOINT
        sidebar.set_class(not auto_show, "hidden")

    async def _check_update(self) -> None:
        from nerdvana_cli import __version__
        from nerdvana_cli.core.updater import (
            cached_or_check,
            format_update_notice,
            is_update_check_enabled,
        )

        try:
            flag = bool(self.settings.session.update_check)
        except Exception:
            flag = True
        if not is_update_check_enabled(flag):
            return

        result = await cached_or_check(__version__)
        if result and result.get("version"):
            with contextlib.suppress(Exception):
                self._add_chat_message(
                    format_update_notice(__version__, result["version"], result.get("url", ""))
                )

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
            return

        self._add_chat_message(f"\n[bold green]> {user_text}[/bold green]", raw_text=user_text)
        self._add_chat_message("[bold cyan]Estelle :[/bold cyan]")

        self._generate_response(user_text)

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

    async def _switch_provider(self, provider_name: str, api_key: str) -> None:
        """Switch to a provider with the given API key."""
        from nerdvana_cli.commands.model_commands import switch_provider
        await switch_provider(self, provider_name, api_key)

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
        try:
            bar = self.query_one("#context-bar", Static)
        except Exception:
            return
        color = "green" if pct < 60 else "yellow" if pct < 80 else "red"
        bar_w   = 20
        filled  = int(bar_w * pct / 100)
        bar_str = "\u2588" * filled + "\u2591" * (bar_w - filled)
        bar.update(Text.from_markup(f"[{color}]ctx [{bar_str}] {pct}%[/{color}]"))
        with contextlib.suppress(Exception):
            self.query_one("#sidebar", Sidebar).set_context(
                provider=self.settings.model.provider,
                model=self.settings.model.model,
                pct=pct,
            )

    def _update_banner(self) -> None:
        """Update the logo banner with current provider/model info."""
        banner = self.query_one("#logo-banner", Static)
        registry_count = len(self._agent_loop.registry.all_tools()) if self._agent_loop else 0
        ctx_k = self.settings.session.max_context_tokens // 1000
        ctx_display = f"{ctx_k}K" if ctx_k < 1000 else f"{ctx_k // 1000}M"
        banner.update(Text.from_markup(
            "[bold bright_white]"
            " ███╗   ██╗███████╗██████╗ ██████╗ ██╗   ██╗ █████╗ ███╗   ██╗ █████╗ \n"
            " ████╗  ██║██╔════╝██╔══██╗██╔══██╗██║   ██║██╔══██╗████╗  ██║██╔══██╗\n"
            " ██╔██╗ ██║█████╗  ██████╔╝██║  ██║██║   ██║███████║██╔██╗ ██║███████║\n"
            " ██║╚██╗██║██╔══╝  ██╔══██╗██║  ██║╚██╗ ██╔╝██╔══██║██║╚██╗██║██╔══██║\n"
            " ██║ ╚████║███████╗██║  ██║██████╔╝ ╚████╔╝ ██║  ██║██║ ╚████║██║  ██║\n"
            " ╚═╝  ╚═══╝╚══════╝╚═╝  ╚═╝╚═════╝   ╚═══╝  ╚═╝  ╚═╝╚═╝  ╚═══╝╚═╝  ╚═╝\n"
            "[/bold bright_white]"
            "[dim]https://nerdvana.kr | Feedback: jinho.von.choi@nerdvana.kr\n"
            f"v{__version__} | {self.settings.model.provider}/{self.settings.model.model} | "
            f"ctx:{ctx_display} | Tools: {registry_count}"
            + (" | Parism" if self.parism_client else "")
            + "[/dim]"
        ))

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

        menu = self.query_one("#command-menu", CommandMenu)
        if event.value.startswith("/"):
            query = event.value.lower()
            menu.clear_options()
            for cmd, desc in SLASH_COMMANDS:
                if query == "/" or cmd.startswith(query):
                    menu.add_option(Option(f"{cmd}  {desc}", id=cmd))
            if self._agent_loop:
                _seen: set[str] = {cmd for cmd, _ in SLASH_COMMANDS}
                for skill in self._agent_loop.skill_loader.list_skills():
                    if skill.trigger not in _seen and (query == "/" or skill.trigger.startswith(query)):
                        menu.add_option(Option(f"{skill.trigger}  {skill.description}", id=skill.trigger))
                        _seen.add(skill.trigger)
            if menu.option_count > 0:
                menu.add_class("visible")
            else:
                menu.remove_class("visible")
        else:
            menu.remove_class("visible")

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        """Handle command menu, model selector, or provider selector."""
        # Provider selector
        if isinstance(event.option_list, ProviderSelector):
            selector = self.query_one("#provider-selector", ProviderSelector)
            selector.remove_class("visible")
            provider_name = event.option.id
            if provider_name:
                from nerdvana_cli.commands.model_commands import handle_provider_selection
                asyncio.create_task(handle_provider_selection(self, provider_name))
            return

        # Model selector
        if isinstance(event.option_list, ModelSelector):
            model_selector = self.query_one("#model-selector", ModelSelector)
            model_selector.remove_class("visible")
            model_id = event.option.id
            if model_id:
                input_widget = self.query_one("#user-input", Input)
                input_widget.value = f"/model {model_id}"
                self.call_later(input_widget.action_submit)
                input_widget.focus()
            return

        # Command menu
        menu = self.query_one("#command-menu", CommandMenu)
        input_widget = self.query_one("#user-input", Input)
        menu.remove_class("visible")
        cmd = event.option.id
        if cmd:
            input_widget.value = cmd
            self.call_later(input_widget.action_submit)

    def action_clear_chat(self) -> None:
        """Clear chat action (Ctrl+L)."""
        asyncio.create_task(self._handle_command("/clear"))

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
        editor        = self.query_one("#editor-pane", EditorPane)
        relative_path = editor.current_path()
        if not relative_path:
            self._add_chat_message("[dim]No editor buffer to save[/dim]", raw_text="No editor buffer to save")
            return

        try:
            self._save_editor_buffer(relative_path, editor.current_text())
        except Exception as exc:
            self._add_chat_message(f"[red]Save failed: {exc}[/red]", raw_text=f"Save failed: {exc}")
            return

        editor.mark_clean()
        self._active_editor_path = relative_path
        self._editor_dirty       = False
        self._add_chat_message(f"[dim]Saved {relative_path}[/dim]", raw_text=f"Saved {relative_path}")

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
        try:
            relative_path = self._relative_project_path(event.path)
            self._open_editor_buffer(relative_path)
        except Exception as exc:
            self._add_chat_message(f"[red]Open failed: {exc}[/red]", raw_text=f"Open failed: {exc}")

    def _open_editor_buffer(self, relative_path: str) -> None:
        """Load a relative project path into the direct editor pane."""
        text   = self._read_editor_buffer(relative_path)
        editor = self.query_one("#editor-pane", EditorPane)
        editor.load_buffer(
            relative_path = relative_path,
            text          = text,
            language      = language_for_path(relative_path),
        )
        editor.show_pane()
        editor.focus_editor()
        self._active_editor_path = relative_path
        self._editor_dirty       = False

    def _read_editor_buffer(self, relative_path: str) -> str:
        """Read a project file through validated, symlink-aware path handling."""
        path_error = validate_path(relative_path, self._project_root)
        if path_error:
            raise PermissionError(path_error)

        fd = safe_open_fd(relative_path, self._project_root, os.O_RDONLY)
        with os.fdopen(fd, "rb") as handle:
            data = handle.read(_MAX_EDITOR_FILE_BYTES + 1)

        if len(data) > _MAX_EDITOR_FILE_BYTES:
            raise ValueError(f"Editor refuses files larger than {_MAX_EDITOR_FILE_BYTES} bytes")
        if b"\x00" in data:
            raise ValueError("Editor refuses binary files")
        return data.decode("utf-8", errors="replace")

    def _save_editor_buffer(self, relative_path: str, content: str) -> None:
        """Write a project file through validated, symlink-aware path handling."""
        path_error = validate_path(relative_path, self._project_root)
        if path_error:
            raise PermissionError(path_error)

        fd = safe_open_fd(
            relative_path,
            self._project_root,
            os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
        )
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)

    def _relative_project_path(self, path: str | Path) -> str:
        """Convert an absolute selected path into a validated project-relative path."""
        root     = Path(self._project_root).resolve()
        selected = Path(path).expanduser().resolve()
        try:
            relative_path = str(selected.relative_to(root))
        except ValueError as exc:
            raise PermissionError(f"Path is outside project root: {selected}") from exc

        path_error = validate_path(relative_path, self._project_root)
        if path_error:
            raise PermissionError(path_error)
        return relative_path
