"""Limits on parallel sub-agent work and on repeated identical tool calls.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from typing import Any

DEFAULT_AGENT_SLOTS = 5

# Semaphores are bound to the event loop that created them, so they are kept
# per loop as well as per provider.
_slots: dict[tuple[int, str], asyncio.Semaphore] = {}


def agent_slot(provider: str, limit: int = DEFAULT_AGENT_SLOTS) -> asyncio.Semaphore:
    """Semaphore bounding concurrent sub-agents that talk to *provider*."""
    key = (id(asyncio.get_running_loop()), provider or "default")
    slot = _slots.get(key)
    if slot is None:
        slot = asyncio.Semaphore(max(1, limit))
        _slots[key] = slot
    return slot


class RepeatDetector:
    """Counts consecutive identical tool calls (same name and arguments).

    ``observe`` returns how many times in a row the call has now been made.
    Calls to tools in *exempt* (status polling) never count.
    """

    def __init__(self, exempt: frozenset[str] = frozenset()) -> None:
        self._exempt    = exempt
        self._signature: str | None = None
        self._count     = 0

    def observe(self, name: str, arguments: Any) -> int:
        """Record a call; return its consecutive repeat count (1 for a new call)."""
        if name in self._exempt:
            return 0
        payload   = json.dumps(arguments, sort_keys=True, ensure_ascii=False, default=str)
        signature = hashlib.sha256(f"{name}\x00{payload}".encode()).hexdigest()
        if signature == self._signature:
            self._count += 1
        else:
            self._signature = signature
            self._count     = 1
        return self._count
