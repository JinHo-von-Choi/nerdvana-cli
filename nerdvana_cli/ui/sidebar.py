"""Left sidebar container — opencode-style, breakpoint-aware."""
from __future__ import annotations

from typing import Any

from textual.app import ComposeResult
from textual.containers import VerticalScroll

from nerdvana_cli.core.task_state import TaskRegistry
from nerdvana_cli.ui.sidebar_sections import (
    SidebarContextSection,
    SidebarFilesSection,
    SidebarHeaderSection,
    SidebarMcpSection,
    SidebarSkillsSection,
    SidebarTasksSection,
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

    def __init__(self, cwd: str = "", **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.styles.width = 35
        self.add_class("hidden")
        import os
        self._cwd = cwd or os.getcwd()

    def compose(self) -> ComposeResult:
        yield SidebarHeaderSection(id="sidebar-header")
        yield SidebarContextSection(id="sidebar-context")
        yield SidebarTasksSection(id="sidebar-tasks")
        yield SidebarToolsSection(id="sidebar-tools")
        yield SidebarMcpSection(id="sidebar-mcp")
        yield SidebarSkillsSection(id="sidebar-skills")
        yield SidebarFilesSection(cwd=self._cwd, id="sidebar-files")

    def set_header(self, topic: str, cwd: str) -> None:
        self.query_one("#sidebar-header", SidebarHeaderSection).set_state(topic, cwd)

    def set_context(self, provider: str, model: str, pct: int) -> None:
        self.query_one("#sidebar-context", SidebarContextSection).set_state(provider, model, pct)

    def set_tools(self, names: list[str]) -> None:
        self.query_one("#sidebar-tools", SidebarToolsSection).set_state(names)

    def set_mcp(self, servers: list[tuple[str, str]]) -> None:
        self.query_one("#sidebar-mcp", SidebarMcpSection).set_state(servers)

    def set_skills(self, triggers: list[str]) -> None:
        self.query_one("#sidebar-skills", SidebarSkillsSection).set_state(triggers)

    def set_tasks_registry(self, registry: TaskRegistry) -> None:
        self.query_one("#sidebar-tasks", SidebarTasksSection).set_registry(registry)

    async def refresh_files(self) -> None:
        """Delegate async git status refresh to SidebarFilesSection."""
        with __import__("contextlib").suppress(Exception):
            await self.query_one("#sidebar-files", SidebarFilesSection).refresh_async()
