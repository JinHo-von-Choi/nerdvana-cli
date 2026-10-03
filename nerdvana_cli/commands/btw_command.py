"""The ``/btw`` command: ask a side question without touching the conversation.

Author: 최진호
Date:   2026-10-03

    /btw <question>

The question goes to the model together with the conversation so far, as one more message, and the answer is shown
in the chat. Neither is added to the history, so the next step of the agent does not see them. The request begins
exactly like the agent's own, so a provider that caches the start of a request reuses it. No tools are offered:
it is an answer from what the conversation already holds.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.markup import escape

if TYPE_CHECKING:
    from nerdvana_cli.ui.app import NerdvanaApp

USAGE = "Usage: /btw <question>"


async def handle_btw(app: NerdvanaApp, args: str) -> None:
    """Handle ``/btw``."""
    question = args.strip()
    loop     = app._agent_loop
    if not question:
        app._add_chat_message(f"[dim]{USAGE}[/dim]")
        return
    if loop is None or app._is_generating:
        app._add_chat_message("[dim]/btw works while the agent is idle.[/dim]")
        return
    messages = loop._to_provider_messages() + [{"role": "user", "content": question}]
    answer: list[str] = []
    error = ""
    async for event in loop.provider.stream(loop.build_system_prompt(), messages, []):
        if event.type == "content_delta":
            answer.append(event.content)
        elif event.type == "usage" and event.usage:
            loop._apply_usage(event.usage, None)
        elif event.type == "error":
            error = event.error
    if error and not answer:
        app._add_chat_message(f"[red]/btw failed: {escape(error)}[/red]")
        return
    text = "".join(answer).strip() or "(no answer)"
    app._add_chat_message(f"[dim]btw: {escape(question)}[/dim]\n{escape(text)}", raw_text=text)
