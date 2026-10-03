"""Text the user typed while the agent was working, held for the model's next step.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations


class InputQueue:
    """Typed-ahead text in the order it was typed."""

    def __init__(self) -> None:
        self._items: list[str] = []

    def put(self, text: str) -> None:
        """Hold *text*, unless it is blank."""
        if text.strip():
            self._items.append(text)

    def pending(self) -> bool:
        """True when typed-ahead text is waiting."""
        return bool(self._items)

    def take(self) -> list[str]:
        """Return and clear the typed-ahead text."""
        taken, self._items = self._items, []
        return taken
