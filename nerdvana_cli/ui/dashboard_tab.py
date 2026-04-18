"""DashboardTab — Textual widget for operational observability.

Renders:
  - Session header (mode, provider/model, accumulated cost)
  - Tool heatmap: top tools by call count (sparkline bars)
  - Failure rate table: per-tool failure % and average latency
  - Live log tail (scrolling)
  - Token sparkline: per-session input/output token counts
  - Health summary footer (/health week-over-week)

Activation:
  - Ctrl+D keybinding in NerdvanaApp
  - /dashboard slash command

The widget is always instantiated but hidden by default; toggling shows it
overlaid on top of the chat area.
"""

from __future__ import annotations

import logging
from typing import Any

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Log, Static

logger = logging.getLogger(__name__)

_SPARKLINE_CHARS = " ▁▂▃▄▅▆▇█"


def _sparkline(values: list[int], width: int = 20) -> str:
    """Render a single-line sparkline string for a list of integers."""
    if not values:
        return " " * width
    max_v = max(values) or 1
    scale = (len(_SPARKLINE_CHARS) - 1) / max_v
    chars = [_SPARKLINE_CHARS[min(int(v * scale), len(_SPARKLINE_CHARS) - 1)] for v in values]
    # Pad or truncate to *width*
    if len(chars) < width:
        chars = [" "] * (width - len(chars)) + chars
    return "".join(chars[-width:])


def _bar(value: int, max_value: int, width: int = 12) -> str:
    """Render a horizontal bar of filled/empty block chars."""
    if max_value <= 0:
        return "░" * width
    filled = min(width, round(value / max_value * width))
    return "█" * filled + "░" * (width - filled)


# ---------------------------------------------------------------------------
# Sub-widgets
# ---------------------------------------------------------------------------

class SessionHeader(Static):
    """Top strip: session info + cost."""

    DEFAULT_CSS = """
    SessionHeader {
        height: 3;
        background: #0f3460;
        color: #e2e8f0;
        padding: 1 2;
        border-bottom: solid #334155;
    }
    """

    def update_info(
        self,
        session_id: str  = "",
        mode:       str  = "",
        context:    str  = "",
        provider:   str  = "",
        model:      str  = "",
        cost_usd:   float = 0.0,
    ) -> None:
        parts: list[str] = []
        if session_id:
            parts.append(f"[dim]session:[/dim] {session_id[:12]}")
        if mode:
            parts.append(f"[dim]mode:[/dim] {mode}")
        if context:
            parts.append(f"[dim]ctx:[/dim] {context}")
        if provider and model:
            parts.append(f"[dim]model:[/dim] {provider}/{model}")
        parts.append(f"[dim]cost:[/dim] [bold green]${cost_usd:.4f}[/bold green]")
        self.update("  |  ".join(parts))


class ToolHeatmap(Static):
    """Heatmap of tool call frequency — sparkline bars per tool."""

    DEFAULT_CSS = """
    ToolHeatmap {
        height: auto;
        min-height: 6;
        border: solid #334155;
        padding: 1;
        background: #1e293b;
    }
    """

    def update_data(self, buckets: list[dict[str, Any]]) -> None:
        if not buckets:
            self.update("[dim]No tool data yet.[/dim]")
            return
        max_count = max((b["count"] for b in buckets), default=1)
        lines = ["[bold]Tool Heatmap[/bold]\n"]
        for b in buckets[:8]:
            name  = b["tool"][:18].ljust(18)
            count = b["count"]
            bar   = _bar(count, max_count, width=14)
            lines.append(f"  {name} {bar} {count:>4}")
        self.update("\n".join(lines))


class FailureRatePanel(Static):
    """Failure rate + average duration per tool."""

    DEFAULT_CSS = """
    FailureRatePanel {
        height: auto;
        min-height: 6;
        border: solid #334155;
        padding: 1;
        background: #1e293b;
    }
    """

    def update_data(self, buckets: list[dict[str, Any]]) -> None:
        if not buckets:
            self.update("[dim]No tool data yet.[/dim]")
            return
        lines = ["[bold]Failure Rate & Latency[/bold]\n"]
        for b in buckets[:8]:
            name     = b["tool"][:18].ljust(18)
            count    = b["count"]
            failures = b["failures"]
            avg_ms   = b["avg_ms"]
            pct      = failures / count * 100 if count else 0
            colour   = "red" if pct > 20 else ("yellow" if pct > 5 else "green")
            lines.append(
                f"  {name} [{colour}]{pct:4.1f}%[/{colour}]  {avg_ms:>5}ms"
            )
        self.update("\n".join(lines))


class TokenSparkline(Static):
    """Token consumption sparkline for the current session."""

    DEFAULT_CSS = """
    TokenSparkline {
        height: 4;
        border: solid #334155;
        padding: 1;
        background: #1e293b;
    }
    """

    _history: list[int]

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._history = []

    def push(self, tokens: int) -> None:
        self._history.append(tokens)
        if len(self._history) > 40:
            self._history.pop(0)
        spark = _sparkline(self._history, width=36)
        total = sum(self._history)
        self.update(f"[bold]Token Sparkline[/bold]\n  {spark}  total={total:,}")


class HealthFooter(Static):
    """7-day health summary footer."""

    DEFAULT_CSS = """
    HealthFooter {
        height: 3;
        background: #16213e;
        color: #94a3b8;
        padding: 0 2;
        border-top: solid #334155;
    }
    """

    def update_summary(self, summary: dict[str, Any]) -> None:
        calls    = summary.get("total_calls", 0)
        tokens   = summary.get("total_tokens", 0)
        cost     = summary.get("total_cost_usd", 0.0)
        failures = summary.get("top_failures", [])
        fail_str = ", ".join(f'{f["tool"]}({f["count"]})' for f in failures[:3]) or "none"
        self.update(
            f"[dim]7-day:[/dim]  {calls:,} calls  |  {tokens:,} tokens  |  "
            f"${cost:.3f}  |  top failures: {fail_str}"
        )


# ---------------------------------------------------------------------------
# DashboardTab
# ---------------------------------------------------------------------------

class DashboardTab(Widget):
    """Full-screen analytics dashboard tab.

    Hidden by default. Toggled via action_toggle_dashboard (Ctrl+D) or /dashboard.
    """

    DEFAULT_CSS = """
    DashboardTab {
        display: none;
        layer: base;
        width: 100%;
        height: 1fr;
        background: #1a1a2e;
        overflow: hidden auto;
    }
    DashboardTab.active {
        display: block;
    }
    #dash-grid {
        height: auto;
    }
    #dash-row-top {
        height: auto;
    }
    #dash-row-mid {
        height: auto;
    }
    #dash-log {
        height: 12;
        border: solid #334155;
        background: #0f172a;
        margin: 0 1;
    }
    """

    _visible: reactive[bool] = reactive(False)

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._reader:       Any = None   # AnalyticsReader, lazily created
        self._session_info: dict[str, Any] = {}

    def on_mount(self) -> None:
        """Start polling refresh every 5 seconds."""
        self.set_interval(5.0, self._refresh)

    def compose(self) -> ComposeResult:
        yield SessionHeader(id="dash-header")
        with Vertical(id="dash-grid"):
            with Horizontal(id="dash-row-top"):
                yield ToolHeatmap(id="dash-heatmap")
                yield FailureRatePanel(id="dash-failures")
            with Horizontal(id="dash-row-mid"):
                yield Log(id="dash-log", max_lines=200, highlight=True)
                yield TokenSparkline(id="dash-sparkline")
        yield HealthFooter(id="dash-footer")

    # ------------------------------------------------------------------
    # Public API (called by app.py)
    # ------------------------------------------------------------------

    def set_session_info(self, **kwargs: Any) -> None:
        self._session_info = kwargs
        try:
            header = self.query_one("#dash-header", SessionHeader)
            header.update_info(**kwargs)
        except Exception:  # noqa: BLE001
            pass

    def push_token_count(self, tokens: int) -> None:
        try:
            sparkline = self.query_one("#dash-sparkline", TokenSparkline)
            sparkline.push(tokens)
        except Exception:  # noqa: BLE001
            pass

    def append_log(self, line: str) -> None:
        try:
            log = self.query_one("#dash-log", Log)
            log.write_line(line)
        except Exception:  # noqa: BLE001
            pass

    def toggle(self) -> None:
        """Show/hide the dashboard."""
        if "active" in self.classes:
            self.remove_class("active")
        else:
            self.add_class("active")
            self._refresh()

    # ------------------------------------------------------------------
    # Internal refresh
    # ------------------------------------------------------------------

    def _refresh(self) -> None:
        if "active" not in self.classes:
            return
        try:
            from nerdvana_cli.core.analytics import AnalyticsReader
            if self._reader is None:
                self._reader = AnalyticsReader()

            buckets = self._reader.recent_tool_buckets()
            summary = self._reader.summary(days=7)

            self.query_one("#dash-heatmap",  ToolHeatmap).update_data(buckets)
            self.query_one("#dash-failures", FailureRatePanel).update_data(buckets)
            self.query_one("#dash-footer",   HealthFooter).update_summary(summary)

            if self._session_info:
                cost = self._reader.session_cost(
                    self._session_info.get("session_id", "")
                )
                self.query_one("#dash-header", SessionHeader).update_info(
                    **{**self._session_info, "cost_usd": cost}
                )
        except Exception as exc:  # noqa: BLE001
            logger.debug("dashboard refresh error: %s", exc)
