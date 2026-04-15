"""Section widgets for the responsive sidebar."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from rich.text import Text
from textual.widget import Widget

from nerdvana_cli.core.task_state import TaskRegistry, TaskStatus
from nerdvana_cli.ui.git_status import fetch_porcelain, parse_porcelain
from nerdvana_cli.ui.task_panel import render_task_row

_MAX_TOPIC_LEN = 30
_MAX_CWD_LEN   = 30


def _truncate(text: str, limit: int = _MAX_TOPIC_LEN) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "\u2026"


def _short_cwd(cwd: str, limit: int = _MAX_CWD_LEN) -> str:
    """Return the cwd as a short path preserving the final component.

    Examples: ~/…/nerdvana-cli, /tmp/…/mydir
    """
    p = Path(cwd).expanduser().resolve()
    home = Path.home()
    try:
        rel = "~/" + str(p.relative_to(home))
    except ValueError:
        rel = str(p)
    if len(rel) <= limit:
        return rel
    # Keep the basename and as much of the prefix as fits
    name = p.name
    prefix = rel[: limit - len(name) - 2]
    return f"{prefix}\u2026/{name}"


class SidebarContextSection(Widget):
    """Provider/model label + context-usage progress bar."""

    DEFAULT_CSS = """
    SidebarContextSection {
        height: auto;
        padding: 0 0 1 0;
        border-top: dashed $accent 30%;
    }
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._provider = ""
        self._model    = ""
        self._pct      = 0

    def set_state(self, provider: str, model: str, pct: int) -> None:
        self._provider = provider
        self._model    = model
        self._pct      = max(0, min(100, pct))
        self.refresh()

    def render(self) -> Text:
        out = Text()
        out.append("MODEL\n", style="bold")
        label = f"{self._provider}/{self._model}"
        out.append(_truncate(label) + "\n", style="cyan")
        bar_w  = 28
        filled = int(bar_w * self._pct / 100)
        color  = "green" if self._pct < 60 else "yellow" if self._pct < 80 else "red"
        out.append("\u2588" * filled, style=color)
        out.append("\u2591" * (bar_w - filled), style="dim")
        out.append(f" {self._pct}%", style=color)
        return out


class _CollapsibleSection(Widget):
    """Base class: a section with a header line the user can click/toggle to expand."""

    DEFAULT_CSS = """
    _CollapsibleSection {
        height: auto;
        padding: 0 0 1 0;
        border-top: dashed $accent 30%;
    }
    _CollapsibleSection:hover {
        background: #1e293b;
    }
    """

    _label: str = "SECTION"

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._expanded = False

    def toggle_expanded(self) -> None:
        self._expanded = not self._expanded
        # layout=True forces Textual to re-measure this widget so height: auto
        # picks up the new line count from render(); plain refresh() only repaints
        # at the cached height, leaving the body hidden after a toggle.
        self.refresh(layout=True)

    def on_click(self) -> None:
        self.toggle_expanded()

    def _header_text(self, count: int) -> Text:
        arrow = "\u25be" if self._expanded else "\u25b8"
        out = Text()
        out.append(f"{arrow} {self._label} ", style="bold")
        out.append(f"({count})", style="cyan")
        return out


class SidebarToolsSection(_CollapsibleSection):
    """Collapsible tools list showing count when collapsed."""

    _label = "TOOLS"

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._tools: list[str] = []

    def set_state(self, tools: list[str]) -> None:
        self._tools = list(tools)
        self.refresh()

    def render(self) -> Text:
        out = self._header_text(len(self._tools))
        if self._expanded:
            for name in self._tools:
                out.append(f"\n  {_truncate(name, 30)}", style="dim")
        return out


_MCP_ICONS: dict[str, tuple[str, str]] = {
    "connected":    ("\u2713", "green"),
    "disconnected": ("\u25cb", "dim"),
    "error":        ("\u2717", "red"),
    "connecting":   ("\u25d0", "yellow"),
}


class SidebarMcpSection(Widget):
    """MCP server list with connection status icons."""

    DEFAULT_CSS = """
    SidebarMcpSection {
        height: auto;
        padding: 0 0 1 0;
        border-top: dashed $accent 30%;
    }
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._servers: list[tuple[str, str]] = []

    def set_state(self, servers: list[tuple[str, str]]) -> None:
        self._servers = list(servers)
        self.refresh()

    def render(self) -> Text:
        out = Text("MCP ", style="bold")
        out.append(f"({len(self._servers)})\n", style="cyan")
        if not self._servers:
            out.append("  (none)", style="dim")
            return out
        for name, status in self._servers:
            icon, color = _MCP_ICONS.get(status, ("?", "dim"))
            out.append(f"  {icon} ", style=color)
            out.append(_truncate(name, 28) + "\n", style=color)
        return out


class SidebarHeaderSection(Widget):
    """Top of sidebar: current session topic + cwd."""

    DEFAULT_CSS = """
    SidebarHeaderSection {
        height: auto;
        padding: 0 0 1 0;
    }
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._topic: str = "(new session)"
        self._cwd: str = str(Path.cwd())

    def set_state(self, topic: str, cwd: str) -> None:
        self._topic = topic or "(new session)"
        self._cwd = cwd
        self.refresh()

    def render(self) -> Text:
        out = Text()
        out.append("\u25cf ", style="bold cyan")
        out.append(_truncate(self._topic) + "\n", style="bold")
        out.append("  " + _short_cwd(self._cwd), style="dim")
        return out


class SidebarSkillsSection(_CollapsibleSection):
    """Collapsible skills list showing count when collapsed."""

    _label = "SKILLS"

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._skills: list[str] = []

    def set_state(self, triggers: list[str]) -> None:
        self._skills = list(triggers)
        self.refresh()

    def render(self) -> Text:
        out = self._header_text(len(self._skills))
        if self._expanded:
            for trig in self._skills:
                out.append(f"\n  {_truncate(trig, 30)}", style="dim")
        return out


class SidebarTasksSection(Widget):
    """Shows active task list migrated from the bottom TaskPanel."""

    DEFAULT_CSS = """
    SidebarTasksSection {
        height: auto;
        padding: 0 0 1 0;
        border-top: dashed $accent 30%;
    }
    """

    def __init__(self, registry: TaskRegistry | None = None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._registry = registry
        self._rows: list[str] = []

    def set_registry(self, registry: TaskRegistry) -> None:
        self._registry = registry
        self.refresh_rows()

    def refresh_rows(self) -> None:
        if self._registry is None:
            self._rows = []
        else:
            self._rows = [render_task_row(t) for t in self._registry.all()]
        self.refresh()

    def render(self) -> Text:
        out = Text("TASKS ", style="bold")
        running = 0
        if self._registry:
            running = sum(1 for t in self._registry.all() if t.status == TaskStatus.RUNNING)
        out.append(f"({running} running)\n", style="cyan")
        if not self._rows:
            out.append("  (none)", style="dim")
            return out
        for row in self._rows:
            out.append(row + "\n", style="dim")
        return out


_FILE_STATUS_STYLE: dict[str, str] = {
    "M": "yellow",
    "A": "green",
    "D": "red",
    "R": "cyan",
    "?": "dim",
}


class SidebarFilesSection(Widget):
    """Modified files from git status, polled every 2 s."""

    DEFAULT_CSS = """
    SidebarFilesSection {
        height: auto;
        max-height: 12;
        padding: 0 0 1 0;
        border-top: dashed $accent 30%;
    }
    """

    def __init__(self, cwd: str, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._cwd  = cwd
        self._rows: list[tuple[str, str]] = []

    async def refresh_async(self) -> None:
        """Fetch porcelain output asynchronously and refresh."""
        raw = await fetch_porcelain(self._cwd)
        self._rows = parse_porcelain(raw)
        self.refresh()

    def render(self) -> Text:
        out = Text("CHANGES ", style="bold")
        out.append(f"({len(self._rows)})\n", style="cyan")
        if not self._rows:
            out.append("  (clean)", style="dim")
            return out
        visible = self._rows[:10]
        for letter, path in visible:
            style = _FILE_STATUS_STYLE.get(letter, "dim")
            out.append(f"  {letter} ", style=f"bold {style}")
            out.append(_truncate(path, 30) + "\n", style=style)
        if len(self._rows) > 10:
            out.append(f"  \u2026+{len(self._rows) - 10} more", style="dim")
        return out
