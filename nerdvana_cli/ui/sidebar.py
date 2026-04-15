"""Left sidebar container — opencode-style, breakpoint-aware."""
from __future__ import annotations

from textual.containers import VerticalScroll


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

    def __init__(self, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self.styles.width = 35
        self.add_class("hidden")
