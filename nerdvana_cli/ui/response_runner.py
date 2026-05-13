"""Streaming response runner for the Textual TUI.

Extracted from ``NerdvanaApp._generate_response`` so the App class focuses on
widget composition and event wiring while the streaming/agent-loop coupling
lives in one self-contained module. The runner takes the App reference and
talks to widgets/state via that handle — no global state, no inheritance.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from typing import TYPE_CHECKING

from rich.text import Text
from textual.containers import VerticalScroll

from nerdvana_cli.core.agent_loop import (
    COMPACT_STATUS_PREFIX,
    CONTEXT_USAGE_PREFIX,
    TOOL_DONE_PREFIX,
    TOOL_STATUS_PREFIX,
)

if TYPE_CHECKING:
    from nerdvana_cli.ui.app import NerdvanaApp
    from nerdvana_cli.ui.widgets import StatusBar, StreamingOutput, ToolStatusLine


async def run_response_stream(app: NerdvanaApp, prompt: str) -> None:
    """Run the agent loop and stream its output into the chat UI.

    Side effects on ``app``:
        - sets ``_is_generating``
        - mutates streaming/tool-status/status-bar widgets
        - calls ``_add_chat_message`` / ``_update_context_usage``
        - reads ``_agent_loop`` / ``settings`` / ``parism_client``
    """
    # Imports kept local to avoid runtime cycle with ui.app at import time.
    from nerdvana_cli.ui.widgets import StatusBar, StreamingOutput, ToolStatusLine

    app._is_generating = True
    streaming:   StreamingOutput  = app.query_one("#streaming-output", StreamingOutput)
    tool_status: ToolStatusLine   = app.query_one("#tool-status", ToolStatusLine)
    status_bar:  StatusBar        = app.query_one("#status-bar", StatusBar)

    streaming.add_class("active")
    streaming.update("")

    start_time    = time.monotonic()
    timer_running = True

    async def _update_thinking_timer() -> None:
        """Periodically refresh the status bar with elapsed thinking time."""
        while timer_running:
            elapsed = time.monotonic() - start_time
            usage   = app._agent_loop.state.usage if app._agent_loop else None
            status_bar.update_status(
                model      = app.settings.model.model,
                provider   = app.settings.model.provider,
                tokens_in  = usage.input_tokens if usage else 0,
                tokens_out = usage.output_tokens if usage else 0,
                tools      = len(app._agent_loop.registry.all_tools()) if app._agent_loop else 0,
                parism     = app.parism_client is not None,
                thinking   = True,
                elapsed_s  = elapsed,
            )
            await asyncio.sleep(0.5)

    timer_task = asyncio.create_task(_update_thinking_timer())

    try:
        accumulated = ""
        chat_frame  = app.query_one("#chat-frame", VerticalScroll)

        assert app._agent_loop is not None

        # Wire thinking-chunk callback to streaming widget
        def _on_thinking_chunk(text: str) -> None:
            streaming.update_thinking(text)

        app._agent_loop._on_thinking_chunk = _on_thinking_chunk

        async for chunk in app._agent_loop.run(prompt):
            if chunk.startswith(CONTEXT_USAGE_PREFIX):
                pct = int(chunk[len(CONTEXT_USAGE_PREFIX):])
                app._update_context_usage(pct)
                continue

            elif chunk.startswith(TOOL_STATUS_PREFIX):
                tool_info = chunk[len(TOOL_STATUS_PREFIX):].replace("[", "\\[")
                tool_status.update(Text.from_markup(f"  [cyan]⟳ {tool_info}[/cyan]"))
                tool_status.add_class("active")
                chat_frame.scroll_end(animate=False)

            elif chunk.startswith(TOOL_DONE_PREFIX):
                tool_info = chunk[len(TOOL_DONE_PREFIX):]
                safe_info = tool_info.replace("[", "\\[")
                if "[error]" in tool_info:
                    tool_status.update(Text.from_markup(f"  [red]✗ {safe_info}[/red]"))
                else:
                    tool_status.update(Text.from_markup(f"  [green]✓ {safe_info}[/green]"))

            elif chunk.startswith(COMPACT_STATUS_PREFIX):
                compact_info = chunk[len(COMPACT_STATUS_PREFIX):]
                if "done" in compact_info or "fallback" in compact_info:
                    with contextlib.suppress(Exception):
                        app.query_one(ToolStatusLine).remove_class("active")
                else:
                    with contextlib.suppress(Exception):
                        tool_status_l = app.query_one(ToolStatusLine)
                        tool_status_l.update("[dim yellow]compressing context...[/dim yellow]")
                        tool_status_l.add_class("active")

            else:
                tool_status.remove_class("active")
                accumulated += chunk
                streaming.update_content(accumulated)
                chat_frame.scroll_end(animate=False)

        # Stop timer
        timer_running = False
        timer_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await timer_task

        tool_status.remove_class("active")
        streaming.remove_class("active")
        streaming.reset()
        thinking_buf = getattr(app._agent_loop, "last_thinking", "")
        if accumulated.strip():
            app._add_chat_message(accumulated, raw_text=accumulated, thinking=thinking_buf)

        # Final status with total elapsed
        elapsed = time.monotonic() - start_time
        usage   = app._agent_loop.state.usage
        app._add_chat_message(
            f"[dim]({elapsed:.1f}s | {usage.input_tokens} in / {usage.output_tokens} out)[/dim]"
        )

        status_bar.update_status(
            model      = app.settings.model.model,
            provider   = app.settings.model.provider,
            tokens_in  = usage.input_tokens,
            tokens_out = usage.output_tokens,
            tools      = len(app._agent_loop.registry.all_tools()),
            parism     = app.parism_client is not None,
        )
    except Exception as e:
        timer_running = False
        timer_task.cancel()
        tool_status.remove_class("active")
        streaming.remove_class("active")
        streaming.update("")
        app._add_chat_message(f"\n[bold red]Error: {e}[/bold red]")
    finally:
        app._is_generating = False
