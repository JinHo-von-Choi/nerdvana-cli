"""ProviderSelector — popup OptionList for provider switching via /provider."""

from __future__ import annotations

from textual.widgets import OptionList


class ProviderSelector(OptionList):
    """Popup selector for provider switching via /provider."""

    DEFAULT_CSS = """
    ProviderSelector {
        layer: overlay;
        dock: bottom;
        height: auto;
        max-height: 15;
        margin: 0 0 3 0;
        border: tall $accent;
        background: $surface;
        display: none;
    }
    ProviderSelector.visible {
        display: block;
    }
    """
