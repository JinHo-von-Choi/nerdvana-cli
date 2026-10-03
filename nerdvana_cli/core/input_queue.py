"""Text the user typed while the agent was working, held for the model's next step.

Author: 최진호
Date:   2026-10-03

In ``queue`` mode (``session.steer_mode``) the text waits for the next step. In ``interrupt`` mode, and for text
sent explicitly as a steer, ``interrupt`` is also set: the loop stops the step in progress (the provider stream
or the running tools, see ``core.cancellation``) and starts the next one with the text. Only a caller that puts
text in the queue can set it, so a run nobody types into (``nerdvana run``, ACP) never sees one.
"""

from __future__ import annotations

import asyncio
from typing import Any

from nerdvana_cli.types import ToolResult

STEER_MODES = ("queue", "interrupt")

# A tool already running when an interruption arrives gets this long to finish by itself before it is cancelled.
TOOL_PATIENCE_SECONDS = 1.0

INTERRUPTED_NOTE = (
    "[Interrupted: the user sent new instructions before this tool finished, so its result was not collected. "
    "It may have run in full or in part.]"
)


class InputQueue:
    """Typed-ahead text in the order it was typed."""

    def __init__(self, mode: str = "queue") -> None:
        self.mode      = mode
        self.interrupt = asyncio.Event()
        self.patience  = TOOL_PATIENCE_SECONDS
        self._items: list[str] = []

    def put(self, text: str, interrupt: bool | None = None) -> bool:
        """Hold *text*, unless it is blank. True when it also interrupts the step in progress.

        *interrupt* ``None`` follows the mode; ``True`` or ``False`` decides for this text.
        """
        if not text.strip():
            return False
        self._items.append(text)
        wanted = self.mode == "interrupt" if interrupt is None else interrupt
        if wanted:
            self.interrupt.set()
        return wanted

    def pending(self) -> bool:
        """True when typed-ahead text is waiting."""
        return bool(self._items)

    def take(self) -> list[str]:
        """Return and clear the typed-ahead text, and with it any interruption it asked for."""
        taken, self._items = self._items, []
        self.interrupt.clear()
        return taken


def interrupted_results(tool_uses: list[dict[str, Any]]) -> list[ToolResult]:
    """One error result per call of a batch that an interruption stopped, so the history stays paired."""
    return [ToolResult(tool_use_id=call["id"], content=INTERRUPTED_NOTE, is_error=True) for call in tool_uses]
