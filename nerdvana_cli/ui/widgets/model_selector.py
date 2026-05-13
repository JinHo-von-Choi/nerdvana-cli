"""ModelSelector — popup OptionList for model switching via /models."""

from __future__ import annotations

from textual.widgets import OptionList


class ModelSelector(OptionList):
    """Popup selector for model switching via /models."""

    DEFAULT_CSS = """
    ModelSelector {
        layer: overlay;
        dock: bottom;
        height: auto;
        max-height: 15;
        margin: 0 0 3 0;
        border: tall $accent;
        background: $surface;
        display: none;
    }
    ModelSelector.visible {
        display: block;
    }
    """
