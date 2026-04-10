"""NerdVana TUI -- Textual-based terminal interface."""

from __future__ import annotations

import asyncio
import contextlib
import os
from typing import Any

from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.events import Paste
from textual.widgets import Footer, Header, Input, OptionList, Static
from textual.widgets.option_list import Option

from nerdvana_cli import __version__
from nerdvana_cli.core.agent_loop import AgentLoop
from nerdvana_cli.core.session import SessionStorage
from nerdvana_cli.core.settings import NerdvanaSettings
from nerdvana_cli.core.task_state import TaskRegistry
from nerdvana_cli.tools.registry import create_tool_registry
from nerdvana_cli.ui.task_panel import TaskPanel


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
        summary = f"[{len(lines)}줄 · {len(text)}자]"

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
    ("/mcp", "MCP server status"),
    ("/tokens", "Show token usage"),
    ("/skills", "List available skills"),
    ("/tools", "List tools"),
    ("/update", "Check and install updates"),
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

    def __init__(self, content: str, raw_text: str = "", **kwargs):
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


class NerdvanaApp(App):
    """Main TUI application."""

    TITLE = "NerdVana CLI"
    CSS_PATH = "styles.tcss"

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
        Binding("ctrl+c", "quit", "Quit", show=True),
        Binding("ctrl+l", "clear_chat", "Clear", show=True),
        Binding("escape", "focus_input", "Input", show=False),
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
        self._loop: AgentLoop | None = None
        self._is_generating = False
        self._pending_provider: str = ""  # provider name awaiting API key input
        self._task_registry = TaskRegistry()

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(id="main-container"):
            yield Static(id="logo-banner")
            with VerticalScroll(id="chat-frame"):
                yield StreamingOutput(id="streaming-output")
                yield ToolStatusLine(id="tool-status")
            yield CommandMenu(id="command-menu")
            yield ProviderSelector(id="provider-selector")
            yield ModelSelector(id="model-selector")
            yield MultilineAwareInput(
                placeholder="Message...",
                id="user-input",
            )
        yield TaskPanel(task_registry=self._task_registry, id="task-panel")
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
        self._loop = AgentLoop(
            settings      = self.settings,
            registry      = registry,
            session       = session,
            task_registry = self._task_registry,
            team_registry = team_registry,
        )

        self._update_banner()

        menu = self.query_one("#command-menu", CommandMenu)
        _seen_triggers: set[str] = set()
        for skill in self._loop.skill_loader.list_skills():
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

    async def _check_update(self) -> None:
        from nerdvana_cli import __version__
        from nerdvana_cli.core.updater import check_for_update

        result = await check_for_update(__version__)
        if result:
            try:
                self._add_chat_message(
                    f"[bold yellow]Update available: {result['version']}[/bold yellow] "
                    f"[dim](current: v{__version__})[/dim]\n"
                    f"[dim]Run /update to install[/dim]"
                )
            except Exception:
                pass

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
        self._add_chat_message("[bold cyan]에스텔 :[/bold cyan]")

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
                usage = self._loop.state.usage if self._loop else None
                status_bar.update_status(
                    model=self.settings.model.model,
                    provider=self.settings.model.provider,
                    tokens_in=usage.input_tokens if usage else 0,
                    tokens_out=usage.output_tokens if usage else 0,
                    tools=len(self._loop.registry.all_tools()) if self._loop else 0,
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

            async for chunk in self._loop.run(prompt):
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
            usage = self._loop.state.usage
            self._add_chat_message(
                f"[dim]({elapsed:.1f}s | {usage.input_tokens} in / {usage.output_tokens} out)[/dim]"
            )

            status_bar.update_status(
                model=self.settings.model.model,
                provider=self.settings.model.provider,
                tokens_in=usage.input_tokens,
                tokens_out=usage.output_tokens,
                tools=len(self._loop.registry.all_tools()),
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
        provider_name = self._pending_provider
        self._pending_provider = ""
        input_widget = self.query_one("#user-input", Input)
        input_widget.placeholder = "Message..."
        input_widget.password = False

        if not api_key.strip():
            self._add_chat_message("[yellow]Cancelled.[/yellow]")
            return

        await self._switch_provider(provider_name, api_key)

    async def _switch_provider(self, provider_name: str, api_key: str) -> None:
        """Switch to a provider with the given API key. Saves config and refreshes UI.

        Note: API key validity is NOT verified here. Empty list_models() result
        means the provider does not expose model enumeration — not that the key
        is invalid. Real key failures surface as 401/403 on the first stream call.
        """
        self._add_chat_message(f"[dim]Switching to {provider_name}...[/dim]")

        from nerdvana_cli.providers.base import DEFAULT_BASE_URLS, DEFAULT_MODELS, ProviderName
        from nerdvana_cli.providers.factory import create_provider

        try:
            prov = ProviderName(provider_name)
        except ValueError:
            self._add_chat_message(f"[red]Unknown provider: {provider_name}[/red]")
            return

        base_url = DEFAULT_BASE_URLS.get(prov, "")
        default_model = DEFAULT_MODELS.get(prov, "")

        # Apply settings unconditionally — key verification is the caller's job.
        self.settings.model.provider = provider_name
        self.settings.model.api_key = api_key
        self.settings.model.base_url = base_url
        self.settings.model.model = default_model
        self._loop.provider = self._loop.create_provider_from_settings()

        self._add_chat_message(f"[dim]Switched to {provider_name}/{default_model}[/dim]")

        # Best-effort model enumeration for the selector. Empty result is fine.
        test_provider = create_provider(
            provider=prov, model=default_model, api_key=api_key, base_url=base_url,
        )
        models: list = []
        try:
            models = await test_provider.list_models()
        except Exception as exc:
            # Surface the reason instead of silently swallowing it.
            self._add_chat_message(
                f"[dim]list_models unavailable for {provider_name}: {type(exc).__name__}: {exc}[/dim]"
            )

        if models:
            selector = self.query_one("#model-selector", ModelSelector)
            selector.clear_options()
            for m in models:
                current = " [current]" if m.id == default_model else ""
                selector.add_option(Option(f"{m.id}{current}", id=m.id))
            self._add_chat_message(f"[dim]{len(models)} models. Select one:[/dim]")
            selector.add_class("visible")
            selector.focus()
        else:
            self._add_chat_message(
                f"[dim]Model enumeration unavailable. Using default: {default_model}[/dim]"
            )

        self._update_banner()
        self.query_one("#status-bar", StatusBar).update_status(
            model=self.settings.model.model,
            provider=self.settings.model.provider,
            tools=len(self._loop.registry.all_tools()),
            parism=self.parism_client is not None,
        )

        # Save config + API key per provider
        from nerdvana_cli.core.setup import load_config, save_config
        existing = load_config()
        existing["model"] = {
            "provider": self.settings.model.provider,
            "model": self.settings.model.model,
            "api_key": self.settings.model.api_key,
            "base_url": self.settings.model.base_url,
            "max_tokens": self.settings.model.max_tokens,
            "temperature": self.settings.model.temperature,
        }
        if "api_keys" not in existing:
            existing["api_keys"] = {}
        existing["api_keys"][provider_name] = api_key
        save_config(existing)
        self._add_chat_message("[dim]Config saved.[/dim]")

    def _show_session_context(self, registry: Any) -> None:
        """Show session startup context summary."""
        from nerdvana_cli.core.nirnamd import load_nirna_files

        parts = []

        # Working directory
        parts.append(f"  cwd: {self.settings.cwd}")

        # NIRNA.md status
        nirna_files = load_nirna_files(cwd=self.settings.cwd)
        if nirna_files:
            for nf in nirna_files:
                parts.append(f"  NIRNA.md ({nf.type}): {nf.path}")
        else:
            parts.append("  NIRNA.md: not found")

        # Tools summary
        tools = registry.all_tools()
        from nerdvana_cli.mcp.tools import McpToolAdapter
        mcp = [t for t in tools if isinstance(t, McpToolAdapter)]
        builtin = [t for t in tools if not isinstance(t, McpToolAdapter)]
        parts.append(f"  Tools: {len(builtin)} built-in, {len(mcp)} MCP")

        # Skills
        if self._loop:
            skills = self._loop.skill_loader.list_skills()
            if skills:
                triggers = ", ".join(s.trigger for s in skills)
                parts.append(f"  Skills: {triggers}")

        # Context window
        ctx_k = self.settings.session.max_context_tokens // 1000
        parts.append(f"  Context: {ctx_k}K tokens")

        summary = "\n".join(parts)
        self._add_chat_message(
            f"[dim]Session initialized:\n{summary}[/dim]",
            raw_text=f"Session initialized:\n{summary}",
        )

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

    def _update_banner(self) -> None:
        """Update the logo banner with current provider/model info."""
        banner = self.query_one("#logo-banner", Static)
        registry_count = len(self._loop.registry.all_tools()) if self._loop else 0
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
        """Handle slash commands."""
        parts   = cmd.split(maxsplit=1)
        command = parts[0].lower()

        if command in ("/quit", "/exit", "/q"):
            self.exit()
        elif command == "/clear":
            if self._loop:
                self._loop.state.messages.clear()
                self._loop.state.turn_count = 1
                self._loop.reset_session()
            self._clear_chat_messages()
            self._add_chat_message("[dim]Conversation cleared.[/dim]")
        elif command == "/init":
            nirna_path = os.path.join(self.settings.cwd, "NIRNA.md")
            exists = os.path.exists(nirna_path)
            self._add_chat_message(
                f"[dim]{'Analyzing existing' if exists else 'Generating'} NIRNA.md...[/dim]"
            )
            init_prompt = (
                f"Analyze this project directory ({self.settings.cwd}) and "
                f"{'suggest improvements to the existing' if exists else 'create a new'} NIRNA.md file.\n\n"
                "NIRNA.md is loaded into every NerdVana CLI session as project instructions. "
                "It must be concise — only include what the AI would get wrong without it.\n\n"
                "What to include:\n"
                "1. Build, test, lint commands (especially non-standard ones)\n"
                "2. High-level architecture (only what requires reading multiple files to understand)\n"
                "3. Code style rules that differ from language defaults\n"
                "4. Required env vars or setup steps\n"
                "5. Non-obvious gotchas\n\n"
                "What NOT to include:\n"
                "- Obvious instructions derivable from the codebase\n"
                "- Generic development practices\n"
                "- Every component or file structure listing\n"
                "- Sensitive information (API keys, tokens)\n\n"
                "Read the key project files first (manifest, README, config), then write the NIRNA.md content.\n"
                + ("Read the existing NIRNA.md first and suggest specific improvements.\n" if exists else "")
                + "Prefix the file with: # NIRNA.md\n"
                "Write the file using FileWrite tool."
            )
            self._generate_response(init_prompt)
        elif command == "/model":
            args_text = parts[1] if len(parts) > 1 else ""
            if args_text:
                from nerdvana_cli.providers.base import detect_provider
                self.settings.model.model = args_text
                self.settings.model.provider = detect_provider(args_text).value
                self._loop.provider = self._loop.create_provider_from_settings()
                self._add_chat_message(
                    f"[dim]Switched to {self.settings.model.provider}/{args_text}[/dim]"
                )
                self.query_one("#status-bar", StatusBar).update_status(
                    model=self.settings.model.model,
                    provider=self.settings.model.provider,
                    tools=len(self._loop.registry.all_tools()),
                    parism=self.parism_client is not None,
                )
                self._update_banner()
            else:
                self._add_chat_message(
                    f"[dim]Model: {self.settings.model.provider}/{self.settings.model.model}[/dim]"
                )
        elif command == "/models":
            self._add_chat_message("[dim]Fetching models...[/dim]")
            try:
                models = await self._loop.provider.list_models()
                if not models:
                    self._add_chat_message("[yellow]No models found or API error.[/yellow]")
                else:
                    selector = self.query_one("#model-selector", ModelSelector)
                    selector.clear_options()
                    for m in models:
                        label = m.id
                        if m.id == self.settings.model.model:
                            label += "  [current]"
                        selector.add_option(Option(label, id=m.id))
                    selector.add_class("visible")
                    selector.focus()
            except Exception as e:
                self._add_chat_message(f"[red]Error listing models: {e}[/red]")
        elif command == "/provider":
            from nerdvana_cli.providers.base import DEFAULT_MODELS, ProviderName
            selector = self.query_one("#provider-selector", ProviderSelector)
            selector.clear_options()
            for prov in ProviderName:
                default_model = DEFAULT_MODELS.get(prov, "")
                current = " [current]" if prov.value == self.settings.model.provider else ""
                selector.add_option(Option(
                    f"{prov.value}  ({default_model}){current}",
                    id=prov.value,
                ))
            selector.add_class("visible")
            selector.focus()
        elif command == "/mcp":
            if self.mcp_manager:
                status = self.mcp_manager.get_status()
                if not status:
                    self._add_chat_message("[dim]No MCP servers configured.[/dim]")
                else:
                    self._add_chat_message("[bold]MCP Servers:[/bold]")
                    for name, connected in status.items():
                        icon = "[green]ON[/green]" if connected else "[red]OFF[/red]"
                        self._add_chat_message(f"  {icon} {name}")
            else:
                self._add_chat_message("[dim]No .mcp.json found.[/dim]")
        elif command == "/tokens":
            if self._loop:
                u = self._loop.state.usage
                self._add_chat_message(
                    f"[dim]Tokens: {u.input_tokens} in / {u.output_tokens} out / {u.total_tokens} total[/dim]"
                )
        elif command == "/tools":
            if self._loop:
                for t in self._loop.registry.all_tools():
                    safe = "R" if t.is_read_only else "W"
                    self._add_chat_message(f"  [{safe}] [bold]{t.name}[/bold]")
        elif command == "/skills":
            if self._loop:
                skills = self._loop.skill_loader.list_skills()
                if skills:
                    for s in skills:
                        self._add_chat_message(f"  [bold]{s.trigger}[/bold] -- {s.description}", raw_text=f"{s.trigger} -- {s.description}")
                else:
                    self._add_chat_message("[dim]No skills found. Add .md files to .nerdvana/skills/[/dim]")
        elif command == "/update":
            from nerdvana_cli.core.updater import run_self_update
            self._add_chat_message("[dim]Checking for updates...[/dim]")
            success, message = run_self_update()
            safe_msg = message.replace("[", "\\[")
            if success:
                self._add_chat_message(f"[green]{safe_msg}[/green]")
            else:
                self._add_chat_message(f"[red]{safe_msg}[/red]")
        elif command == "/help":
            self._add_chat_message(
                "[bold]Commands[/bold]\n"
                "/help    -- Show help\n"
                "/quit    -- Exit\n"
                "/clear   -- Clear chat\n"
                "/init    -- Generate NIRNA.md\n"
                "/model   -- Show/change model\n"
                "/models     -- List available models\n"
                "/provider   -- Add/switch provider\n"
                "/mcp     -- MCP server status\n"
                "/skills  -- List available skills\n"
                "/tokens  -- Show usage\n"
                "/tools   -- List tools\n"
                "/update  -- Check and install updates\n"
                "Ctrl+C   -- Quit\n"
                "Ctrl+L   -- Clear chat"
            )
        else:
            if self._loop:
                skill = self._loop.skill_loader.get_by_trigger(command)
                if skill:
                    self._loop.activate_skill(skill.body)
                    self._add_chat_message(f"[dim]Skill activated: {skill.name}[/dim]", raw_text=f"Skill activated: {skill.name}")
                    if len(parts) > 1:
                        input_widget = self.query_one("#user-input", Input)
                        input_widget.value = parts[1]
                        self.call_later(input_widget.action_submit)
                    return
            self._add_chat_message(f"[red]Unknown command: {command}[/red]")

    def on_input_changed(self, event: Input.Changed) -> None:
        """Show/hide command menu based on input."""
        # 사용자가 요약을 직접 편집하면 멀티라인 원본을 버린다.
        # call_after_refresh 콜백이 아직 실행되지 않은 동안은 보존한다.
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
            if self._loop:
                _seen: set[str] = {cmd for cmd, _ in SLASH_COMMANDS}
                for skill in self._loop.skill_loader.list_skills():
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
                # Check for saved API key
                from nerdvana_cli.core.setup import load_config
                existing = load_config()
                saved_keys = existing.get("api_keys", {})
                saved_key = saved_keys.get(provider_name, "")

                # Also check env vars
                if not saved_key:
                    from nerdvana_cli.providers.base import PROVIDER_KEY_ENVVARS, ProviderName
                    try:
                        for var in PROVIDER_KEY_ENVVARS.get(ProviderName(provider_name), []):
                            saved_key = os.environ.get(var, "")
                            if saved_key:
                                break
                    except ValueError:
                        pass

                if saved_key:
                    # Key exists — switch directly
                    asyncio.create_task(self._switch_provider(provider_name, saved_key))
                else:
                    # No key — ask for input
                    self._pending_provider = provider_name
                    self._add_chat_message(
                        f"[dim]Enter API key for {provider_name}:[/dim]"
                    )
                    input_widget = self.query_one("#user-input", Input)
                    input_widget.placeholder = f"API key for {provider_name}..."
                    input_widget.password = True
                    input_widget.focus()
            return

        # Model selector
        if isinstance(event.option_list, ModelSelector):
            selector = self.query_one("#model-selector", ModelSelector)
            selector.remove_class("visible")
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
