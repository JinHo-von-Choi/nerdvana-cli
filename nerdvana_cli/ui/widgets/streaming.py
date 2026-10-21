"""StreamingOutput — in-place updating widget for streaming LLM output."""

from __future__ import annotations

from rich.text import Text
from textual.widgets import Static


class StreamingOutput(Static):
    """In-place updating widget for streaming LLM output."""

    DEFAULT_CSS = """
    StreamingOutput {
        height: auto;
        max-height: 50%;
        padding: 0 1;
        display: none;
    }
    StreamingOutput.active {
        display: block;
    }
    """

    _thinking: str = ""
    _content:  str = ""

    def update_thinking(self, text: str) -> None:
        """Replace the current thinking preview."""
        self._thinking = text
        self._refresh_display()

    def update_content(self, text: str) -> None:
        """Replace the current streamed content."""
        self._content = text
        self._refresh_display()

    def reset(self) -> None:
        """Clear both thinking and content buffers."""
        self._thinking = ""
        self._content  = ""
        self.update("")

    def _refresh_display(self) -> None:
        # Model text is shown as typed: brackets in code must not be read as Rich markup.
        shown = Text()
        if self._thinking:
            shown.append(self._thinking, style="dim italic")
        if self._thinking and self._content:
            shown.append("\n\n")
        if self._content:
            shown.append(self._content)
        self.update(shown)
