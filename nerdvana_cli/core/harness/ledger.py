"""The budget identity of a P-P-C run: what it spends, and what it must spend.

Task 2.2 of the Harness2/DSH enhancement plan.

A Propose–Probe–Compose run spends a fixed budget: for a parameter ``k`` it runs
``R = 4k`` rollouts (``k`` base, ``k`` invariant candidates, ``k`` domain
candidates, ``k`` final synthesis) and holds ``P = 3k`` proposer sessions
(``k`` invariant, ``k`` domain, ``k`` synthesis). The identity is not a target
but a check: a run that spent something else was miscounted somewhere, and the
ledger says so instead of letting the difference pass unnoticed.

Based on Google Research Harness2 (arXiv:2610.14224).

Author: 최진호
Date:   2026-10-20
"""

from __future__ import annotations

from dataclasses import dataclass, field

# The rollout kinds, in the order a run produces them.
ROLLOUT_KINDS: tuple[str, ...] = ("base", "inv", "dom", "agg")

# The proposer session kinds, in the order a run produces them.
SESSION_KINDS: tuple[str, ...] = ("prop_inv", "prop_dom", "comp_agg")


class BudgetIdentityError(RuntimeError):
    """The run spent something other than the budget its parameter states."""


def expected_rollouts(k: int) -> int:
    """The rollouts a run with parameter *k* must spend: 4k."""
    return 4 * k


def expected_sessions(k: int) -> int:
    """The proposer sessions a run with parameter *k* must hold: 3k."""
    return 3 * k


@dataclass
class HarnessLedger:
    """What one P-P-C run spent, and whether it spent the budget it declared.

    The labels are kept in the order they were recorded, not in a mapping: a
    label that appears twice is the overwrite bug the identity has to catch, so
    the evidence must survive until the check runs.
    """

    mode:    str
    task_id: str
    k:       int
    rollouts: list[tuple[str, float]] = field(default_factory=list)
    sessions: list[tuple[str, float]] = field(default_factory=list)

    def record_rollout(self, label: str, usd: float = 0.0) -> None:
        """One rollout of the run, with what it cost."""
        self.rollouts.append((label, float(usd)))

    def record_session(self, label: str, usd: float = 0.0) -> None:
        """One proposer session of the run, with what it cost."""
        self.sessions.append((label, float(usd)))

    @property
    def rollout_usd(self) -> float:
        """What the rollouts cost."""
        return sum(usd for _label, usd in self.rollouts)

    @property
    def session_usd(self) -> float:
        """What the proposer sessions cost."""
        return sum(usd for _label, usd in self.sessions)

    @property
    def total_usd(self) -> float:
        """What the whole run cost."""
        return self.rollout_usd + self.session_usd

    def assert_identity(self) -> None:
        """Check the budget identity: the counts, their composition and the labels.

        Raises ``BudgetIdentityError`` when a label appears twice, when the run
        spent the wrong number of rollouts or sessions, or when the kinds do
        not add up to the parameter.
        """
        self._assert_no_duplicates("rollout", [label for label, _ in self.rollouts])
        self._assert_no_duplicates("session", [label for label, _ in self.sessions])

        if len(self.rollouts) != expected_rollouts(self.k):
            raise BudgetIdentityError(
                f"ROLLOUT COUNT: {len(self.rollouts)} != {expected_rollouts(self.k)} "
                f"(expected R=4k with k={self.k})"
            )
        if len(self.sessions) != expected_sessions(self.k):
            raise BudgetIdentityError(
                f"SESSION COUNT: {len(self.sessions)} != {expected_sessions(self.k)} "
                f"(expected P=3k with k={self.k})"
            )

        counts = _count_kinds([label for label, _ in self.rollouts], ROLLOUT_KINDS)
        for kind in ROLLOUT_KINDS:
            if counts[kind] != self.k:
                raise BudgetIdentityError(
                    f"ROLLOUT COMPOSITION: {kind}={counts[kind]} != k={self.k}"
                )
        counts = _count_kinds([label for label, _ in self.sessions], SESSION_KINDS)
        for kind in SESSION_KINDS:
            if counts[kind] != self.k:
                raise BudgetIdentityError(
                    f"SESSION COMPOSITION: {kind}={counts[kind]} != k={self.k}"
                )

    @staticmethod
    def _assert_no_duplicates(what: str, labels: list[str]) -> None:
        """Refuse a label that appears twice: one of them was overwritten."""
        seen: set[str] = set()
        for label in labels:
            if label in seen:
                raise BudgetIdentityError(f"DUPLICATE {what} label: {label!r}")
            seen.add(label)


def _count_kinds(labels: list[str], kinds: tuple[str, ...]) -> dict[str, int]:
    """How many labels of each kind the list holds."""
    counts = dict.fromkeys(kinds, 0)
    for label in labels:
        for kind in kinds:
            if label.startswith(kind):
                counts[kind] += 1
                break
    return counts
