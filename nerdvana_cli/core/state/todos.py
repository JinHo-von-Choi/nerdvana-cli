"""Session todo lists: storage location and the continuation guard.

Author: 최진호
Date:   2026-10-03

``TodoWrite`` stores one list per session. The guard reads that list when the
model ends its turn: unfinished items earn a nudge to keep going, but only
while the nudges produce progress, so an impossible item cannot hold the loop.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from nerdvana_cli.core.config import paths

_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9._-]+$")

NONE     = "none"
CONTINUE = "continue"
STALLED  = "stalled"


def sanitize_session_id(raw: str) -> str:
    """Return *raw* when it is a safe file stem, otherwise ``"default"``."""
    if raw and raw not in {".", ".."} and _SESSION_ID_RE.match(raw):
        return raw
    return "default"


def todos_dir() -> Path:
    """Directory holding one ``<session>.json`` todo list per session."""
    directory = paths.user_data_home() / "todos"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def load_todos(session_id: str, directory: Path | None = None) -> list[dict[str, Any]]:
    """The session's todo items, or an empty list when none were written."""
    path = (directory or todos_dir()) / f"{sanitize_session_id(session_id)}.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    items = data.get("todos") if isinstance(data, dict) else None
    return [item for item in items or [] if isinstance(item, dict)]


def open_items(todos: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Items not yet completed."""
    return [item for item in todos if item.get("status") != "completed"]


def describe(items: list[dict[str, Any]]) -> str:
    """One line per item: status and content."""
    return "\n".join(f"- [{item.get('status', '?')}] {item.get('content', '')}" for item in items)


@dataclass(frozen=True)
class TodoDecision:
    """What the loop should do at the end of a turn."""

    kind:    str
    message: str = ""


class TodoGuard:
    """Nudges the model back to unfinished todos until progress stops."""

    def __init__(self, max_stalls: int = 3) -> None:
        self._max_stalls     = max_stalls
        self._last_completed: int | None = None
        self._stalls         = 0

    def reset(self) -> None:
        """Start counting afresh, e.g. for a new user prompt."""
        self._last_completed = None
        self._stalls         = 0

    def check(self, session_id: str, directory: Path | None = None) -> TodoDecision:
        """Decide whether the turn that just ended may stay ended."""
        todos   = load_todos(session_id, directory)
        pending = open_items(todos)
        if not pending:
            self.reset()
            return TodoDecision(NONE)

        completed = len(todos) - len(pending)
        if self._last_completed is not None and completed <= self._last_completed:
            self._stalls += 1
        else:
            self._stalls = 0
        self._last_completed = completed

        if self._stalls >= self._max_stalls:
            self.reset()
            return TodoDecision(
                STALLED,
                f"Stopped: {len(pending)} todo item(s) are still open and the last "
                f"{self._max_stalls} attempts made no progress.\n{describe(pending)}",
            )
        return TodoDecision(
            CONTINUE,
            f"You ended your turn with {len(pending)} unfinished todo item(s):\n{describe(pending)}\n"
            "Continue working on them. If an item cannot be done, update the list with TodoWrite "
            "and say why, or ask the user with AskUser when a decision is theirs.",
        )
