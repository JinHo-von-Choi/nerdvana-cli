"""ChatMessage — clickable Static widget for a single chat turn."""

from __future__ import annotations

from typing import Any

from textual.widgets import Static


class ChatMessage(Static):
    """Clickable chat message block. Click to copy content."""

    DEFAULT_CSS = """
    ChatMessage {
        padding: 0 1;
        width: 100%;
    }
    ChatMessage:hover {
        background: #1e293b;
    }
    ChatMessage.-copied {
        background: #064e3b;
    }
    """

    def __init__(self, content: str, raw_text: str = "", **kwargs: Any) -> None:
        super().__init__(content, **kwargs)
        self._raw_text = raw_text or self._strip_markup(content)

    @staticmethod
    def _strip_markup(text: str) -> str:
        """Remove Rich markup tags for clipboard content."""
        import re
        return re.sub(r'\[/?[^\]]+\]', '', text)

    def on_click(self) -> None:
        from nerdvana_cli.ui.clipboard import copy_to_clipboard
        success = copy_to_clipboard(self._raw_text)
        if success:
            self.add_class("-copied")
            self.set_timer(1.0, lambda: self.remove_class("-copied"))
            self.app.notify("Copied!", timeout=1)
