"""The ``/steer`` command: give the agent new instructions without waiting for its step to end.

Author: 최진호
Date:   2026-10-03

    /steer <text>

While the agent works, the text interrupts the step in progress (the model's response or the running tools, see
``core.cancellation``) and the next step starts with it, whatever ``session.steer_mode`` says. When the agent is
idle it is an ordinary prompt. Ctrl+T sends what is typed in the input line the same way.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.markup import escape

if TYPE_CHECKING:
    from nerdvana_cli.ui.app import NerdvanaApp

USAGE = "Usage: /steer <text>"


async def handle_steer(app: NerdvanaApp, args: str) -> None:
    """Handle ``/steer``."""
    text = args.strip()
    loop = app._agent_loop
    if not text:
        app._add_chat_message(f"[dim]{USAGE}[/dim]")
        return
    if loop is None or not app._is_generating:
        app._start_prompt(text, text)
        return
    loop.queue_input(text, interrupt=True)
    app._add_chat_message(f"\n[bold green]> {escape(text)}[/bold green] [dim](interrupting the current step)[/dim]", raw_text=text)
