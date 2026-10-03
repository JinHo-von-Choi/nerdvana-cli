"""The compaction block a provider returns when it summarizes a conversation itself.

Author: 최진호
Date:   2026-10-03

A message that carries one stands for every message before it: the provider sends the block and leaves the
earlier messages out, while the loop keeps its own history whole. Whatever estimates or restores a history has
to know where that block is.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any

COMPACTION_BLOCK = "compaction"


def carries_compaction(blocks: Iterable[Any]) -> bool:
    """True when one of the provider *blocks* is a compaction block."""
    return any(isinstance(block, dict) and block.get("type") == COMPACTION_BLOCK for block in blocks)


def last_compaction_index(messages: Sequence[Any]) -> int:
    """The index of the last message that carries a compaction block; 0 when none does."""
    for index in range(len(messages) - 1, -1, -1):
        if carries_compaction(getattr(messages[index], "provider_blocks", None) or ()):
            return index
    return 0
