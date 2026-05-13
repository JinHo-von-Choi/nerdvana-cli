"""ToolStatusLine — single-line tool execution status, overwrites in place."""

from __future__ import annotations

from textual.widgets import Static


class ToolStatusLine(Static):
    """Single-line tool execution status, overwrites in place."""

    DEFAULT_CSS = """
    ToolStatusLine {
        height: 1;
        padding: 0 1;
        color: $text-muted;
        display: none;
    }
    ToolStatusLine.active {
        display: block;
    }
    """
