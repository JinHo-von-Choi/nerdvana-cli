"""Unique tool call identifiers within one conversation.

Author: 최진호
Date:   2026-10-03

Providers require every tool call id in a request to be unique, and reject the
whole request otherwise. An id is only the key that pairs a call with its result
inside our own history, so it can always be replaced by a fresh one as long as
the call and its result are changed together.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Sequence
from typing import Any

from nerdvana_cli.types import Role

# The shortest id limit among the providers we talk to (OpenAI: 40 characters).
_MAX_ID_LENGTH = 40


def new_tool_use_id(name: str, taken: Iterable[str] = ()) -> str:
    """A fresh id that is not in *taken*; ``call_<name>_<8 hex>`` when it fits."""
    taken = set(taken)
    while True:
        candidate = f"call_{name}_{uuid.uuid4().hex[:8]}"
        if len(candidate) > _MAX_ID_LENGTH or not name:
            candidate = f"call_{uuid.uuid4().hex[:24]}"
        if candidate not in taken:
            return candidate


def collect_tool_use_ids(messages: Sequence[Any]) -> set[str]:
    """Every tool call id requested by an assistant message in *messages*."""
    return {
        str(tool_use.get("id", ""))
        for message in messages
        if message.role == Role.ASSISTANT and message.tool_uses
        for tool_use in message.tool_uses
    }


def repair_tool_ids(messages: Sequence[Any]) -> int:
    """Make tool call ids unique across *messages*, in place; return how many changed.

    A call whose id was already used earlier (or is empty) gets a new id, and the
    results that answer it, which follow its assistant message, are re-pointed in
    the same order. The first use of an id keeps it, so a healthy history is not
    touched and a repaired one stays stable on the next pass.
    """
    seen:    set[str]            = set()
    pending: dict[str, list[str]] = {}
    changed = 0

    for message in messages:
        if message.role == Role.ASSISTANT:
            pending = {}
            if not message.tool_uses:
                continue
            repaired: list[dict[str, Any]] = []
            for tool_use in message.tool_uses:
                old = str(tool_use.get("id", ""))
                if old and old not in seen:
                    new, entry = old, tool_use
                else:
                    new   = new_tool_use_id(str(tool_use.get("name", "")), seen)
                    entry = {**tool_use, "id": new}
                    changed += 1
                seen.add(new)
                pending.setdefault(old, []).append(new)
                repaired.append(entry)
            message.tool_uses = repaired
        elif message.role == Role.TOOL:
            queue = pending.get(str(message.tool_use_id or ""))
            if queue:
                message.tool_use_id = queue.pop(0)
    return changed
