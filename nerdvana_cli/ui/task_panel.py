"""TaskPanel — real-time subagent status widget for the NerdVana TUI."""
from __future__ import annotations

from typing import Any

from textual.reactive import reactive
from textual.widget import Widget

from nerdvana_cli.core.task_state import TaskRegistry, TaskState, TaskStatus

_POLL_INTERVAL = 0.5  # seconds


def _status_icon(status: TaskStatus) -> str:
    return {
        TaskStatus.RUNNING:   "●",
        TaskStatus.COMPLETED: "✓",
        TaskStatus.FAILED:    "✗",
        TaskStatus.PENDING:   "○",
        TaskStatus.KILLED:    "⊘",
    }.get(status, "?")


def _render_row(task: TaskState) -> str:
    icon = _status_icon(task.status)
    name = task.description[:24].ljust(24)
    tool = (task.current_tool or "").ljust(10)
    tok  = str(task.tokens_used) if task.tokens_used else ""
    return f" {icon} {name}  {tool}  {tok} tok"


class TaskPanel(Widget):
    """Polls TaskRegistry every 0.5 s and renders agent rows.

    Auto-collapses (height=0) when no tasks are registered.
    """

    DEFAULT_CSS = """
    TaskPanel {
        height: auto;
        max-height: 10;
        border-top: solid $accent;
        background: $surface;
        padding: 0 1;
    }
    TaskPanel.hidden {
        height: 0;
        border: none;
        padding: 0;
    }
    """

    _rows: reactive[str] = reactive("")

    def __init__(self, task_registry: TaskRegistry, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._registry = task_registry

    def on_mount(self) -> None:
        self.set_interval(_POLL_INTERVAL, self._refresh_rows)

    def _refresh_rows(self) -> None:
        tasks = self._registry.all()
        if not tasks:
            self.add_class("hidden")
            self._rows = ""
            return
        self.remove_class("hidden")
        running_count = sum(1 for t in tasks if t.status == TaskStatus.RUNNING)
        header = f"── TASKS ─── [{running_count} running] ─"
        rows   = [header] + [_render_row(t) for t in tasks]
        self._rows = "\n".join(rows)
        self.refresh()

    def render(self) -> str:
        return self._rows or ""
