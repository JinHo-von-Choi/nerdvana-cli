"""Live panels of the Textual TUI: the sidebar sections and the context bar.

Extracted from ``NerdvanaApp``. Each function pushes the current state of the app into one
panel; the App calls them on a timer or after a response and passes itself as the handle.

작성자: 최진호
작성일: 2026-10-03
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import TYPE_CHECKING

from rich.text import Text
from textual.widgets import Static

from nerdvana_cli.ui.sidebar import Sidebar
from nerdvana_cli.ui.sidebar_sections import SidebarTasksSection

if TYPE_CHECKING:
    from nerdvana_cli.ui.app import NerdvanaApp


def refresh_mcp_section(app: NerdvanaApp) -> None:
    """Update sidebar MCP section from mcp_manager.get_status()."""
    if not app.mcp_manager:
        return
    status_map = app.mcp_manager.get_status()
    servers: list[tuple[str, str]] = [
        (name, "connected" if ok else "error")
        for name, ok in status_map.items()
    ]
    with contextlib.suppress(Exception):
        app.query_one("#sidebar", Sidebar).set_mcp(servers)


def refresh_sidebar_tasks(app: NerdvanaApp) -> None:
    """Refresh sidebar task rows when the widget is still mounted."""
    with contextlib.suppress(Exception):
        app.query_one("#sidebar-tasks", SidebarTasksSection).refresh_rows()


def schedule_sidebar_file_refresh(app: NerdvanaApp) -> None:
    """Schedule async sidebar file refresh when the sidebar is still mounted."""
    with contextlib.suppress(Exception):
        sidebar = app.query_one("#sidebar", Sidebar)
        asyncio.create_task(sidebar.refresh_files())


def update_context_usage(app: NerdvanaApp, pct: int) -> None:
    """Update the context usage bar and the sidebar context section."""
    try:
        bar = app.query_one("#context-bar", Static)
    except Exception:
        return
    color = "green" if pct < 60 else "yellow" if pct < 80 else "red"
    bar_w   = 20
    filled  = int(bar_w * pct / 100)
    bar_str = "█" * filled + "░" * (bar_w - filled)
    bar.update(Text.from_markup(f"[{color}]ctx [{bar_str}] {pct}%[/{color}]"))
    with contextlib.suppress(Exception):
        app.query_one("#sidebar", Sidebar).set_context(
            provider=app.settings.model.provider,
            model=app.settings.model.model,
            pct=pct,
        )
