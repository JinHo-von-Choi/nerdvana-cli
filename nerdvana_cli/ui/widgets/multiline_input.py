"""MultilineAwareInput — collapses multi-line pastes into a compact summary."""

from __future__ import annotations

from textual.events import Paste
from textual.widgets import Input


class MultilineAwareInput(Input):
    """Input widget that collapses multi-line paste into a compact summary.

    The original text is preserved in `_pending_multiline` and used when
    the user submits the form.
    """

    _pending_multiline: str | None = None
    _setting_summary:   bool       = False

    def _on_paste(self, event: Paste) -> None:
        # Normalize line endings — terminals may send \r\n or bare \r
        text = event.text.replace('\r\n', '\n').replace('\r', '\n')

        if '\n' not in text:
            self._pending_multiline = None
            return  # let Textual's Input._on_paste handle single-line

        lines = [ln for ln in text.splitlines() if ln.strip()]
        if not lines:
            return

        self._pending_multiline = text
        summary = f"[{len(lines)} lines · {len(text)} chars]"

        def _apply_summary() -> None:
            self._setting_summary = True
            try:
                self.value           = summary
                self.cursor_position = len(summary)
            finally:
                self._setting_summary = False

        # Schedule after the current event cycle so any parent-handler
        # inserts are already done and we can safely overwrite.
        self.call_after_refresh(_apply_summary)
