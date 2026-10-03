"""Turning the questions a user keeps answering "yes" to into rules they can adopt.

Author: 최진호
Date:   2026-10-03

Every time the agent asks for permission and the user answers, the tool, its main argument and the answer
are recorded. A call that was allowed again and again and never refused is a candidate for an
``always_allow`` rule that names exactly that call (``Bash(git status)``). Nothing here changes the
configuration: the suggestions are printed for the user to paste.

Only exact calls are suggested. A command or path pattern that generalises ("every ``git`` command", "every
file under ``src``") would allow calls the user never saw, so that choice is left to the user.

The action classifier in shadow mode (``permissions.classifier``) leaves its verdict next to what happened to
the same call; ``compare_verdicts`` sets the two against each other, which is the evidence for or against
moving to ``enforce``.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from typing import Any

from nerdvana_cli.core.safety.policy import _SHELL_META, _SHELL_TOOLS

MIN_APPROVALS = 3
_GLOB_CHARS   = re.compile(r"[*?\[\]]")
_ARG_LIMIT    = 300


@dataclass(frozen=True)
class Comparison:
    """The classifier's shadow verdicts against what really happened to the calls it judged."""

    judged:               int    # verdicts that are allow, ask or deny
    errors:               int    # calls the classifier failed on (they would have been asked)
    answered:             int    # judged calls the user decided: they were asked and said yes or no
    agreed:               int    # of those, the classifier said allow and the user allowed, or ask/deny and the user refused
    would_ask_allowed:    int    # the classifier would have asked, the user allowed
    would_deny_allowed:   int    # the classifier would have denied, the user allowed
    would_allow_refused:  int    # the classifier would have allowed, the user refused
    interrupts:           int    # calls that ran unasked and the classifier would have asked or denied
    unasked:              int    # judged calls that ran unasked

    @property
    def agreement(self) -> float | None:
        """Share of the user's own answers the classifier matched; None when the user answered none."""
        return self.agreed / self.answered if self.answered else None


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


def compare_verdicts(rows: list[dict[str, Any]]) -> Comparison:
    """Count the shadow-mode verdicts against their outcomes; *rows* carry ``mode``, ``verdict``, ``outcome`` and ``count``.

    Only ``shadow`` rows are compared: in ``enforce`` mode the verdict decided the outcome, so the two are not
    independent. An outcome of ``allow_user`` or ``deny_user`` is the user's own answer; ``allow_auto`` ran unasked.
    """
    tally: Counter[tuple[str, str]] = Counter()
    for row in rows:
        if row["mode"] == "shadow":
            tally[(str(row["verdict"]), str(row["outcome"]))] += int(row["count"])

    def count(verdicts: tuple[str, ...], outcome: str) -> int:
        return sum(tally[(verdict, outcome)] for verdict in verdicts)

    judged   = sum(n for (verdict, _), n in tally.items() if verdict != "error")
    answered = sum(n for (verdict, outcome), n in tally.items() if verdict != "error" and outcome in ("allow_user", "deny_user"))
    return Comparison(
        judged              = judged,
        errors              = sum(n for (verdict, _), n in tally.items() if verdict == "error"),
        answered            = answered,
        agreed              = count(("allow",), "allow_user") + count(("ask", "deny"), "deny_user"),
        would_ask_allowed   = count(("ask",), "allow_user"),
        would_deny_allowed  = count(("deny",), "allow_user"),
        would_allow_refused = count(("allow",), "deny_user"),
        interrupts          = count(("ask", "deny"), "allow_auto"),
        unasked             = count(("allow", "ask", "deny"), "allow_auto"),
    )
