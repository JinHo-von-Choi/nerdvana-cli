"""Turning the questions a user keeps answering "yes" to into rules they can adopt.

Author: 최진호
Date:   2026-10-03

Every time the agent asks for permission and the user answers, the tool, its main argument and the answer
are recorded. A call that was allowed again and again and never refused is a candidate for an
``always_allow`` rule that names exactly that call (``Bash(git status)``). Nothing here changes the
configuration: the suggestions are printed for the user to paste.

Only exact calls are suggested. A command or path pattern that generalises ("every ``git`` command", "every
file under ``src``") would allow calls the user never saw, so that choice is left to the user.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from nerdvana_cli.core.policy import _SHELL_META, _SHELL_TOOLS

MIN_APPROVALS = 3
_GLOB_CHARS   = re.compile(r"[*?\[\]]")
_ARG_LIMIT    = 300


@dataclass(frozen=True)
class Suggestion:
    """A rule the user's own answers support."""

    rule:      str
    approvals: int


def normalise(argument: str | None) -> str:
    """The form in which an argument is stored and compared: trimmed, spaces collapsed, bounded."""
    return re.sub(r"\s+", " ", (argument or "").strip())[:_ARG_LIMIT]


def suggest_rules(rows: list[dict[str, Any]], min_approvals: int = MIN_APPROVALS, existing: list[str] | None = None) -> list[Suggestion]:
    """Rules for calls approved at least *min_approvals* times and never refused.

    *rows* carry ``tool``, ``argument``, ``allowed`` and ``denied`` counts. A call is left out when it has
    no argument, when a shell command has operators (the policy would not honour a rule for it), when its
    text contains glob characters (the rule could not name it exactly) or when a rule already covers it.
    """
    known = set(existing or [])
    found: list[Suggestion] = []
    for row in rows:
        tool, argument = str(row["tool"]), normalise(row.get("argument"))
        if not argument or row["denied"] > 0 or row["allowed"] < min_approvals or _GLOB_CHARS.search(argument):
            continue
        if tool in _SHELL_TOOLS and _SHELL_META.search(argument):
            continue
        rule = f"{tool}({argument})"
        if rule in known or tool in known:
            continue
        found.append(Suggestion(rule, int(row["allowed"])))
    return sorted(found, key=lambda s: (-s.approvals, s.rule))
