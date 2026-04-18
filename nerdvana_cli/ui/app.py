"""NerdVana TUI -- Textual-based terminal interface."""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any

from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.events import Paste
from textual.widgets import Footer, Header, Input, OptionList, Static
from textual.widgets.option_list import Option

from nerdvana_cli import __version__
from nerdvana_cli.core.agent_loop import AgentLoop
from nerdvana_cli.core.session import SessionStorage
from nerdvana_cli.core.settings import NerdvanaSettings
from nerdvana_cli.core.task_state import TaskRegistry
from nerdvana_cli.tools.registry import create_tool_registry
from nerdvana_cli.ui.dashboard_tab import DashboardTab
from nerdvana_cli.ui.sidebar import Sidebar
from nerdvana_cli.ui.sidebar_sections import SidebarTasksSection


class MultilineAwareInput(Input):
    """Input widget that collapses multi-line paste into a compact summary.

    The original text is preserved in `_pending_multiline` and used when
    the user submits the form.
    """

    _pending_multiline: str | None = None
    _setting_summary:   bool       = False

    def _on_paste(self, event: Paste) -> None:
        # Normalize line endings — terminals may send \r\n or bare \r
        text = event.text.replace('\r\n', '\n').replace('\r', '\n')

        if '\n' not in text:
            self._pending_multiline = None
            return  # let Textual's Input._on_paste handle single-line

        lines = [ln for ln in text.splitlines() if ln.strip()]
        if not lines:
            return

        self._pending_multiline = text
        summary = f"[{len(lines)} lines · {len(text)} chars]"

        def _apply_summary() -> None:
            self._setting_summary = True
            try:
                self.value           = summary
                self.cursor_position = len(summary)
            finally:
                self._setting_summary = False

        # Schedule after the current event cycle so any parent-handler
        # inserts are already done and we can safely overwrite.
        self.call_after_refresh(_apply_summary)


SLASH_COMMANDS = [
    ("/help", "Show help"),
    ("/clear", "Clear chat"),
    ("/init", "Generate NIRNA.md"),
    ("/model", "Show/change model"),
    ("/models", "List available models"),
    ("/provider", "Add/switch provider"),
    ("/mode", "Activate/deactivate mode profile"),
    ("/context", "Set context profile"),
    ("/mcp", "MCP server status"),
    ("/tokens", "Show token usage"),
    ("/skills", "List available skills"),
    ("/tools", "List tools"),
    ("/update", "Check and install updates"),
    ("/memories", "List project memories"),
    ("/undo", "Restore pre-edit git checkpoint"),
    ("/redo", "Re-apply last undone checkpoint"),
    ("/checkpoints", "List session checkpoints"),
    ("/route-knowledge", "Classify content → suggest WriteMemory scope"),
    ("/dashboard", "Toggle observability dashboard"),
    ("/health", "Show 7-day tool call health summary"),
    ("/quit", "Exit"),
]


class CommandMenu(OptionList):
    """Popup menu for slash commands."""

    DEFAULT_CSS = """
    CommandMenu {
        layer: overlay;
        dock: bottom;
        height: auto;
        max-height: 10;
        margin: 0 0 3 0;
        border: tall $accent;
        background: $surface;
        display: none;
    }
    CommandMenu.visible {
        display: block;
    }
    """

    def on_mount(self) -> None:
        for cmd, desc in SLASH_COMMANDS:
            self.add_option(Option(f"{cmd}  {desc}", id=cmd))


class ProviderSelector(OptionList):
    """Popup selector for provider switching via /provider."""

    DEFAULT_CSS = """
    ProviderSelector {
        layer: overlay;
        dock: bottom;
        height: auto;
        max-height: 15;
        margin: 0 0 3 0;
        border: tall $accent;
        background: $surface;
        display: none;
    }
    ProviderSelector.visible {
        display: block;
    }
    """


class ModelSelector(OptionList):
    """Popup selector for model switching via /models."""

    DEFAULT_CSS = """
    ModelSelector {
        layer: overlay;
        dock: bottom;
        height: auto;
        max-height: 15;
        margin: 0 0 3 0;
        border: tall $accent;
        background: $surface;
        display: none;
    }
    ModelSelector.visible {
        display: block;
    }
    """


class ChatMessage(Static):
    """Clickable chat message block. Click to copy content."""

    DEFAULT_CSS = """
    ChatMessage {
        padding: 0 1;
        width: 100%;
    }
    ChatMessage:hover {
        background: #1e293b;
    }
    ChatMessage.-copied {
        background: #064e3b;
    }
    """

    def __init__(self, content: str, raw_text: str = "", **kwargs: Any) -> None:
        super().__init__(content, **kwargs)
        self._raw_text = raw_text or self._strip_markup(content)

    @staticmethod
    def _strip_markup(text: str) -> str:
        """Remove Rich markup tags for clipboard content."""
        import re
        return re.sub(r'\[/?[^\]]+\]', '', text)

    def on_click(self) -> None:
        from nerdvana_cli.ui.clipboard import copy_to_clipboard
        success = copy_to_clipboard(self._raw_text)
        if success:
            self.add_class("-copied")
            self.set_timer(1.0, lambda: self.remove_class("-copied"))
            self.app.notify("Copied!", timeout=1)


class StreamingOutput(Static):
    """In-place updating widget for streaming LLM output."""

    DEFAULT_CSS = """
    StreamingOutput {
        height: auto;
        max-height: 50%;
        padding: 0 1;
        display: none;
    }
    StreamingOutput.active {
        display: block;
    }
    """


class ToolStatusLine(Static):
    """Single-line tool execution status, overwrites in place."""

    DEFAULT_CSS = """
    ToolStatusLine {
        height: 1;
        padding: 0 1;
        color: $text-muted;
        display: none;
    }
    ToolStatusLine.active {
        display: block;
    }
    """


class StatusBar(Static):
    """Bottom status bar showing model, tokens, session info."""

    def update_status(
        self,
        model: str    = "",
        provider: str = "",
        tokens_in: int  = 0,
        tokens_out: int = 0,
        tools: int = 0,
        parism: bool = False,
        thinking: bool = False,
        elapsed_s: float = 0.0,
    ) -> None:
        parts: list[str] = []
        if thinking:
            elapsed_str = f"{elapsed_s:.1f}s" if elapsed_s < 60 else f"{elapsed_s / 60:.1f}m"
            token_str = ""
            if tokens_in or tokens_out:
                token_str = f" | {tokens_in + tokens_out} tokens"
            parts.append(f"thinking ({elapsed_str}{token_str})")
        if provider and model:
            parts.append(f"{provider}/{model}")
        if not thinking and (tokens_in or tokens_out):
            parts.append(f"tokens: {tokens_in} in / {tokens_out} out")
        if tools:
            tool_text = f"tools: {tools}"
            if parism:
                tool_text += " (Parism)"
            parts.append(tool_text)
        self.update(" | ".join(parts) if parts else "Ready")


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
        Binding("ctrl+l", "clear_chat",       "Clear",     show=True),
        Binding("ctrl+d", "toggle_dashboard", "Dashboard", show=True),
        Binding("escape", "focus_input",      "Input",     show=False),
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
        self._session_topic: str = ""

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="body"):
            yield Sidebar(id="sidebar")
            with Vertical(id="main-container"):
                yield Static(id="logo-banner")
                with VerticalScroll(id="chat-frame"):
                    yield StreamingOutput(id="streaming-output")
                    yield ToolStatusLine(id="tool-status")
                yield DashboardTab(id="dashboard-tab")
                yield CommandMenu(id="command-menu")
                yield ProviderSelector(id="provider-selector")
                yield ModelSelector(id="model-selector")
                yield MultilineAwareInput(
                    placeholder="Message...",
                    id="user-input",
                )
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
        self._agent_loop = AgentLoop(
            settings      = self.settings,
            registry      = registry,
            session       = session,
            task_registry = self._task_registry,
            team_registry = team_registry,
        )

        self._update_banner()

        import os
        sidebar = self.query_one("#sidebar", Sidebar)
        sidebar.set_header(topic=self._session_topic, cwd=os.getcwd())
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
        self.set_interval(0.5, lambda: self.query_one("#sidebar-tasks", SidebarTasksSection).refresh_rows())
        self.set_interval(2.0, lambda: asyncio.create_task(sidebar.refresh_files()))

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

    def on_resize(self, event: object) -> None:
        """Apply the 140-col breakpoint unless the user has explicitly toggled."""
        sidebar = self.query_one("#sidebar", Sidebar)
        if self._sidebar_user_visible is not None:
            return
        auto_show = self.size.width >= self._SIDEBAR_BREAKPOINT
        sidebar.set_class(not auto_show, "hidden")

    async def _check_update(self) -> None:
        from nerdvana_cli import __version__
        from nerdvana_cli.core.updater import check_for_update

        result = await check_for_update(__version__)
        if result:
            with contextlib.suppress(Exception):
                self._add_chat_message(
                    f"[bold yellow]Update available: {result['version']}[/bold yellow] "
                    f"[dim](current: v{__version__})[/dim]\n"
                    f"[dim]Run /update to install[/dim]"
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
            import os
            self.query_one("#sidebar", Sidebar).set_header(
                topic=self._session_topic,
                cwd=os.getcwd(),
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
        """Run agent loop and stream response to chat."""
        import time

        self._is_generating = True
        streaming   = self.query_one("#streaming-output", StreamingOutput)
        tool_status = self.query_one("#tool-status", ToolStatusLine)
        status_bar  = self.query_one("#status-bar", StatusBar)

        streaming.add_class("active")
        streaming.update("")

        start_time = time.monotonic()
        timer_running = True

        async def _update_thinking_timer() -> None:
            """Periodically update status bar with elapsed time."""
            while timer_running:
                elapsed = time.monotonic() - start_time
                usage = self._agent_loop.state.usage if self._agent_loop else None
                status_bar.update_status(
                    model=self.settings.model.model,
                    provider=self.settings.model.provider,
                    tokens_in=usage.input_tokens if usage else 0,
                    tokens_out=usage.output_tokens if usage else 0,
                    tools=len(self._agent_loop.registry.all_tools()) if self._agent_loop else 0,
                    parism=self.parism_client is not None,
                    thinking=True,
                    elapsed_s=elapsed,
                )
                await asyncio.sleep(0.5)

        timer_task = asyncio.create_task(_update_thinking_timer())

        try:
            accumulated = ""
            chat_frame = self.query_one("#chat-frame", VerticalScroll)

            from nerdvana_cli.core.agent_loop import (
                COMPACT_STATUS_PREFIX,
                CONTEXT_USAGE_PREFIX,
                TOOL_DONE_PREFIX,
                TOOL_STATUS_PREFIX,
            )

            assert self._agent_loop is not None
            async for chunk in self._agent_loop.run(prompt):
                if chunk.startswith(CONTEXT_USAGE_PREFIX):
                    pct = int(chunk[len(CONTEXT_USAGE_PREFIX):])
                    self._update_context_usage(pct)
                    continue

                elif chunk.startswith(TOOL_STATUS_PREFIX):
                    tool_info = chunk[len(TOOL_STATUS_PREFIX):].replace("[", "\\[")
                    tool_status.update(Text.from_markup(f"  [cyan]\u27f3 {tool_info}[/cyan]"))
                    tool_status.add_class("active")
                    chat_frame.scroll_end(animate=False)

                elif chunk.startswith(TOOL_DONE_PREFIX):
                    tool_info = chunk[len(TOOL_DONE_PREFIX):]
                    safe_info = tool_info.replace("[", "\\[")
                    if "[error]" in tool_info:
                        tool_status.update(Text.from_markup(f"  [red]\u2717 {safe_info}[/red]"))
                    else:
                        tool_status.update(Text.from_markup(f"  [green]\u2713 {safe_info}[/green]"))

                elif chunk.startswith(COMPACT_STATUS_PREFIX):
                    compact_info = chunk[len(COMPACT_STATUS_PREFIX):]
                    if "done" in compact_info or "fallback" in compact_info:
                        with contextlib.suppress(Exception):
                            self.query_one(ToolStatusLine).remove_class("active")
                    else:
                        with contextlib.suppress(Exception):
                            tool_status = self.query_one(ToolStatusLine)
                            tool_status.update("[dim yellow]compressing context...[/dim yellow]")
                            tool_status.add_class("active")

                else:
                    tool_status.remove_class("active")
                    accumulated += chunk
                    streaming.update(accumulated)
                    chat_frame.scroll_end(animate=False)

            # Stop timer
            timer_running = False
            timer_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await timer_task

            tool_status.remove_class("active")
            streaming.remove_class("active")
            streaming.update("")
            if accumulated.strip():
                self._add_chat_message(accumulated, raw_text=accumulated)

            # Final status with total elapsed
            elapsed = time.monotonic() - start_time
            usage = self._agent_loop.state.usage
            self._add_chat_message(
                f"[dim]({elapsed:.1f}s | {usage.input_tokens} in / {usage.output_tokens} out)[/dim]"
            )

            status_bar.update_status(
                model=self.settings.model.model,
                provider=self.settings.model.provider,
                tokens_in=usage.input_tokens,
                tokens_out=usage.output_tokens,
                tools=len(self._agent_loop.registry.all_tools()),
                parism=self.parism_client is not None,
            )
        except Exception as e:
            timer_running = False
            timer_task.cancel()
            tool_status.remove_class("active")
            streaming.remove_class("active")
            streaming.update("")
            self._add_chat_message(f"\n[bold red]Error: {e}[/bold red]")
        finally:
            self._is_generating = False

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

    def _add_chat_message(self, markup: str, raw_text: str = "") -> None:
        """Add a clickable chat message to the chat frame."""
        chat_frame = self.query_one("#chat-frame", VerticalScroll)
        streaming = self.query_one("#streaming-output", StreamingOutput)
        msg = ChatMessage(markup, raw_text=raw_text)
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
        """Handle slash commands via dispatching to command modules."""
        from nerdvana_cli.commands import (
            memory_commands,
            model_commands,
            observability_commands,
            profile_commands,
            session_commands,
            system_commands,
        )

        parts = cmd.split(maxsplit=1)
        command = parts[0].lower()
        args = parts[1] if len(parts) > 1 else ""

        handlers = {
            "/model":           model_commands.handle_model,
            "/models":          model_commands.handle_models,
            "/provider":        model_commands.handle_provider,
            "/clear":           session_commands.handle_clear,
            "/tokens":          session_commands.handle_tokens,
            "/tools":           session_commands.handle_tools,
            "/mcp":             session_commands.handle_mcp,
            "/skills":          session_commands.handle_skills,
            "/help":            system_commands.handle_help,
            "/update":          system_commands.handle_update,
            "/init":            system_commands.handle_init,
            "/setup":           system_commands.handle_init,
            "/undo":            memory_commands.handle_undo,
            "/redo":            memory_commands.handle_redo,
            "/checkpoints":     memory_commands.handle_checkpoints,
            "/memories":        memory_commands.handle_memories,
            "/route-knowledge": memory_commands.handle_route_knowledge,
            "/mode":            profile_commands.handle_mode,
            "/context":         profile_commands.handle_context,
            "/health":          observability_commands.handle_health,
            "/dashboard":       observability_commands.handle_dashboard,
        }

        if command in ("/quit", "/exit", "/q"):
            self.exit()
            return

        handler = handlers.get(command)
        if handler:
            await handler(self, args)
            return

        # Skill trigger fallback
        if self._agent_loop:
            skill = self._agent_loop.skill_loader.get_by_trigger(command)
            if skill:
                self._agent_loop.activate_skill(skill.body)
                self._add_chat_message(
                    f"[dim]Skill activated: {skill.name}[/dim]",
                    raw_text=f"Skill activated: {skill.name}",
                )
                if args:
                    input_widget = self.query_one("#user-input", Input)
                    input_widget.value = args
                    self.call_later(input_widget.action_submit)
                return

        self._add_chat_message(f"[red]Unknown command: {command}[/red]")

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

    def action_toggle_sidebar(self) -> None:
        """Toggle sidebar visibility. Sets a user-override that suppresses on_resize."""
        sidebar = self.query_one("#sidebar", Sidebar)
        currently_hidden = "hidden" in sidebar.classes
        self._sidebar_user_visible = currently_hidden
        sidebar.set_class(not currently_hidden, "hidden")

    def action_toggle_dashboard(self) -> None:
        """Toggle observability dashboard (Ctrl+D)."""
        try:
            self.query_one("#dashboard-tab", DashboardTab).toggle()
        except Exception:  # noqa: BLE001
            pass
