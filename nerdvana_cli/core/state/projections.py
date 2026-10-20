"""Pure synchronous session projections with a watermark consistency cut.

Task 1.3 of the Harness2/DSH enhancement plan.

A session transcript is an append-only event log. What the TUI and the CLI read
from it — the open turn, the failed attempts, the compaction lock — is a
projection of that log: a pure fold of the events into a state, read at one
watermark, so that every projection of a snapshot agrees on how far the log has
been read.

A projection unit never touches the disk and never calls out: ``apply`` folds
one event into a state and returns the new state, and ``view`` turns that state
into what a reader sees. The cost of a committed event is O(U) for U units,
and a snapshot is O(U) as well.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, TypeVar

S = TypeVar("S")


@dataclass(frozen=True)
class SessionEventRecord:
    """Immutable committed event in the append-only session log."""

    seq: int
    kind: str
    payload: dict[str, Any]


@dataclass(frozen=True)
class ProjectionSnapshot:
    """Consistent read cut across all registered session projections."""

    as_of_seq: int
    values: dict[str, Any]


class ProjectionUnit(Protocol[S]):
    """Pure synchronous state-driven fold unit."""

    key: str
    state_version: int

    def init(self) -> S: ...

    def apply(self, state: S, event: SessionEventRecord) -> S: ...

    def view(self, state: S) -> Any: ...


class TurnBoundaryProjection:
    """Tracks open turns, completed turns, and attempts that never became history."""

    key           = "turn_boundary"
    state_version = 1

    def init(self) -> dict[str, Any]:
        return {"open_turn": None, "completed_turns": 0, "failed_attempts": 0}

    def apply(self, state: dict[str, Any], event: SessionEventRecord) -> dict[str, Any]:
        if event.kind == "turn/start":
            return {**state, "open_turn": event.payload.get("turn")}
        if event.kind == "turn/end":
            return {
                **state,
                "open_turn": None,
                "completed_turns": state["completed_turns"] + 1,
            }
        if event.kind == "assistant/attempt":
            return {**state, "failed_attempts": state["failed_attempts"] + 1}
        return state

    def view(self, state: dict[str, Any]) -> dict[str, Any]:
        return dict(state)


class CompactionLockProjection:
    """Detects bracketed compaction locks and the orphans a crash leaves behind."""

    key           = "compaction_lock"
    state_version = 1

    def init(self) -> dict[str, Any]:
        return {"orphaned": False, "active_turn": None, "compaction_count": 0, "shadowed_seqs": 0}

    def apply(self, state: dict[str, Any], event: SessionEventRecord) -> dict[str, Any]:
        if event.kind == "compaction/start":
            return {**state, "orphaned": True, "active_turn": event.payload.get("turn")}
        if event.kind == "compaction/summary":
            return {**state, "shadowed_seqs": int(event.payload.get("shadowed_seqs", 0) or 0)}
        if event.kind == "compaction/end":
            failed = bool(event.payload.get("error"))
            return {
                **state,
                "orphaned": False,
                "active_turn": None,
                "compaction_count": state["compaction_count"] + (0 if failed else 1),
            }
        return state

    def view(self, state: dict[str, Any]) -> dict[str, Any]:
        return dict(state)


class ProjectionRegistry:
    """Folds committed session events synchronously across all units.

    Time complexity: O(U) per committed event where U is the number of units.
    """

    def __init__(self) -> None:
        self._units:   dict[str, ProjectionUnit[Any]] = {}
        self._states:  dict[str, Any]                = {}
        self._as_of_seq: int                         = -1

    def register(self, unit: ProjectionUnit[Any]) -> None:
        """Add a projection unit; its key says what it projects."""
        if unit.key in self._units:
            raise ValueError(f"Duplicate projection key: {unit.key}")
        self._units[unit.key]  = unit
        self._states[unit.key] = unit.init()

    def commit(self, event: SessionEventRecord) -> None:
        """Fold one event into every unit and move the watermark to it."""
        if event.seq <= self._as_of_seq:
            raise ValueError(f"Non-monotonic event seq {event.seq} <= {self._as_of_seq}")
        for key, unit in self._units.items():
            self._states[key] = unit.apply(self._states[key], event)
        self._as_of_seq = event.seq

    def state_of(self, key: str) -> Any:
        """What the unit under *key* sees right now."""
        if key not in self._units:
            raise KeyError(f"Unknown projection key: {key}")
        return self._units[key].view(self._states[key])

    def snapshot(self) -> ProjectionSnapshot:
        """Every unit's view at one watermark."""
        return ProjectionSnapshot(
            as_of_seq=self._as_of_seq,
            values={key: unit.view(self._states[key]) for key, unit in self._units.items()},
        )


def default_registry() -> ProjectionRegistry:
    """A registry with the units the session storage keeps."""
    registry = ProjectionRegistry()
    registry.register(TurnBoundaryProjection())
    registry.register(CompactionLockProjection())
    return registry
