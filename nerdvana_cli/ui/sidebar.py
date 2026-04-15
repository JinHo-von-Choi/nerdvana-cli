"""Left sidebar container — opencode-style, breakpoint-aware."""
from __future__ import annotations

from typing import Any

from textual.app import ComposeResult
from textual.containers import VerticalScroll

from nerdvana_cli.ui.sidebar_sections import (
    SidebarContextSection,
    SidebarHeaderSection,
    SidebarToolsSection,
)


class Sidebar(VerticalScroll):
    """Fixed-width (35 cols) left sidebar. Hidden by default.

    Visibility is driven externally by NerdvanaApp.on_resize and
    action_toggle_sidebar — do not toggle classes from inside this widget.
    """

    DEFAULT_CSS = """
    Sidebar {
        dock: left;
        width: 35;
        border-right: solid $accent;
        background: $surface;
        padding: 0 1;
        scrollbar-gutter: stable;
    }
    Sidebar.hidden {
        display: none;
    }
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.styles.width = 35
        self.add_class("hidden")

    def compose(self) -> ComposeResult:
        yield SidebarHeaderSection(id="sidebar-header")
        yield SidebarContextSection(id="sidebar-context")
        yield SidebarToolsSection(id="sidebar-tools")

    def set_header(self, topic: str, cwd: str) -> None:
        self.query_one("#sidebar-header", SidebarHeaderSection).set_state(topic, cwd)

    def set_context(self, provider: str, model: str, pct: int) -> None:
        self.query_one("#sidebar-context", SidebarContextSection).set_state(provider, model, pct)

    def set_tools(self, names: list[str]) -> None:
        self.query_one("#sidebar-tools", SidebarToolsSection).set_state(names)
