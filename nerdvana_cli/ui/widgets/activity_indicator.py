"""ActivityIndicator — single-line widget showing the current AgentLoop activity phase."""

from __future__ import annotations

import time

from textual.reactive import reactive
from textual.widgets import Static

from nerdvana_cli.core.activity_state import ActivityState


class ActivityIndicator(Static):
    """Single-line indicator showing the current AgentLoop activity phase."""

    DEFAULT_CSS = """
    ActivityIndicator {
        height: 1;
        width: 100%;
        padding: 0 1;
        background: $surface;
        color: $text-muted;
    }
    ActivityIndicator.-thinking { color: $warning; }
    ActivityIndicator.-tool     { color: $accent;  }
    ActivityIndicator.-stream   { color: $success; }
    ActivityIndicator.-waiting  { color: $warning; }
    ActivityIndicator.-idle     { color: $text-muted; }
    """

    state: reactive[ActivityState] = reactive(ActivityState)

    _ICONS: dict[str, str] = {
        "idle":         "●",
        "thinking":     "◐",
        "waiting_api":  "◔",
        "streaming":    "◑",
        "tool_running": "◉",
    }
    _CLASS_MAP: dict[str, str] = {
        "idle":         "-idle",
        "thinking":     "-thinking",
        "waiting_api":  "-waiting",
        "streaming":    "-stream",
        "tool_running": "-tool",
    }

    def watch_state(self, new_state: ActivityState) -> None:
        for css in self._CLASS_MAP.values():
            self.remove_class(css)
        self.add_class(self._CLASS_MAP.get(new_state.phase, "-idle"))
        self._refresh_label(new_state)

    def _refresh_label(self, state: ActivityState) -> None:
        icon    = self._ICONS.get(state.phase, "●")
        elapsed = ""
        if state.started_at is not None:
            secs = int(time.time() - state.started_at)
            if secs >= 1:
                elapsed = f" [{secs}s]"
        detail = f": {state.detail}" if state.detail else ""
        self.update(f"{icon} {state.label}{detail}{elapsed}")
