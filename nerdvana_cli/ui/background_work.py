"""TUI work that runs beside the chat: connecting MCP servers, and the user's status line command."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from rich.markup import escape

from nerdvana_cli.ui.live_panels import refresh_mcp_section
from nerdvana_cli.ui.widgets import StatusBar

if TYPE_CHECKING:
    from nerdvana_cli.ui.app import NerdvanaApp


async def connect_mcp_servers(app: NerdvanaApp) -> None:
    """Connect the MCP servers in the background and add their tools; the input stays usable meanwhile."""
    from nerdvana_cli.tools.registry import attach_mcp_tools

    app.notify("Connecting MCP servers...", timeout=3)
    statuses = await app.mcp_manager.connect_all()
    loop     = app._agent_loop
    if loop is not None:
        attach_mcp_tools(loop.registry, app.mcp_manager.get_all_tools(), loop.skill_loader)
    failed = [name for name, status in statuses.items() if not status.startswith("connected")]
    connected = len(statuses) - len(failed)
    note = f"MCP: {connected} of {len(statuses)} server(s) connected" + (f"; failed: {', '.join(failed)}" if failed else "")
    app._add_chat_message(f"[dim]{escape(note)}[/dim]")
    refresh_mcp_section(app)


def refresh_statusline(app: NerdvanaApp) -> None:
    """Run session.statusline_command in the background and show its output in the status bar."""
    command = app.settings.session.statusline_command
    loop    = app._agent_loop
    if not command or loop is None:
        return
    payload = {
        "model": app.settings.model.model, "provider": app.settings.model.provider, "cwd": app.settings.cwd,
        "session_id": loop.session.session_id, "input_tokens": loop.state.usage.input_tokens,
        "output_tokens": loop.state.usage.output_tokens, "cost_usd": loop.total_cost_usd(),
    }

    async def show() -> None:
        from nerdvana_cli.ui.statusline import run_statusline

        app.query_one("#status-bar", StatusBar).set_extra(await run_statusline(command, payload, app.settings.cwd))

    app._statusline_task = asyncio.create_task(show())
