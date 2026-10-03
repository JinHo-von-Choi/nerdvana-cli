"""A goal the agent is held to: an objective and a command that decides whether it was reached.

Author: 최진호
Date:   2026-10-03

State lives in one JSON file per session under the data home, written after every change, so a
resumed session or a restarted process picks the goal up where it stood. A goal moves through
``active`` (the agent is working), ``paused`` (kept, not enforced), ``met`` (the command passed),
``unmet`` (the attempts ran out) and ``cleared`` (dropped by the user).
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import re
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from nerdvana_cli.core import paths

logger = logging.getLogger(__name__)

ACTIVE, PAUSED, MET, UNMET, CLEARED = "active", "paused", "met", "unmet", "cleared"
STATUSES = (ACTIVE, PAUSED, MET, UNMET, CLEARED)
DEFAULT_MAX_ATTEMPTS = 5
_SAFE_ID = re.compile(r"[^A-Za-z0-9_.-]")


class GoalError(ValueError):
    """The goal cannot be created or changed as asked."""


@dataclass
class Goal:
    """One objective with its verification command and how far the agent got."""

    objective:    str
    verify:       str
    status:       str                 = ACTIVE
    attempts:     int                 = 0
    max_attempts: int                 = DEFAULT_MAX_ATTEMPTS
    created_at:   float               = field(default_factory=time.time)
    last_exit:    int | None          = None
    last_tail:    str                 = ""

    def __post_init__(self) -> None:
        if not self.verify.strip():
            raise GoalError("a goal needs a verification command")
        if self.status not in STATUSES:
            raise GoalError(f"unknown goal status {self.status!r}")
        if self.max_attempts < 1:
            raise GoalError("max_attempts must be at least 1")

    @property
    def enforced(self) -> bool:
        """True while the agent is held to the goal."""
        return self.status == ACTIVE

    def record_attempt(self, passed: bool, exit_code: int, tail: str) -> None:
        """Fold in one run of the verification command and move the status."""
        self.attempts  += 1
        self.last_exit  = exit_code
        self.last_tail  = tail
        if passed:
            self.status = MET
        elif self.attempts >= self.max_attempts:
            self.status = UNMET

    def describe(self) -> str:
        """A short status line."""
        state = self.status if self.status != ACTIVE else f"active, {self.attempts} of {self.max_attempts} verification attempts used"
        return f"{state}: {self.objective} (verify: {self.verify})"

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Goal:
        known = set(cls.__dataclass_fields__)
        return cls(**{key: value for key, value in data.items() if key in known})


def _goal_path(session_id: str) -> Path:
    return paths.user_data_home() / "goals" / f"{_SAFE_ID.sub('_', session_id) or 'default'}.json"


def save_goal(session_id: str, goal: Goal | None) -> None:
    """Write the goal atomically; ``None`` removes the file."""
    path = _goal_path(session_id)
    if goal is None:
        path.unlink(missing_ok=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp = tempfile.mkstemp(dir=path.parent, prefix=".goal-", suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(asdict(goal), stream, ensure_ascii=False)
        os.replace(temp, path)
    except OSError:
        with contextlib.suppress(OSError):
            os.unlink(temp)
        raise


def load_goal(session_id: str) -> Goal | None:
    """The saved goal of a session, or None when there is none or the file is unusable."""
    path = _goal_path(session_id)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return Goal.from_dict(data) if isinstance(data, dict) else None
    except FileNotFoundError:
        return None
    except (OSError, ValueError, TypeError) as exc:
        logger.warning("goal file %s ignored: %s", path, exc)
        return None

