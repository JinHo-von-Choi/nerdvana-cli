"""Immutable loop iteration state for AgentLoop.

Centralises all per-iteration state into a single frozen dataclass.
State transitions are performed exclusively via .evolve(), which returns
a new instance — the original is never mutated.
"""

from __future__ import annotations

import dataclasses
from typing import Literal


@dataclasses.dataclass(frozen=True)
class LoopState:
    """Immutable snapshot of one agent loop iteration.

    Fields:
        iteration:          Current iteration counter (1-based).
        stop_reason:        Last stop reason reported by the provider.
        continuation_hint:  Optional text hint for context-limit continuation.
        token_budget_used:  Estimated token count at the start of this turn.
        session_id:         Identifier of the session this loop belongs to.
    """

    iteration:         int
    stop_reason:       Literal["continue", "end_turn", "max_tokens", "tool_error"]
    continuation_hint: str | None
    token_budget_used: int
    session_id:        str

    def evolve(self, **changes: object) -> LoopState:
        """Return a new LoopState with the specified fields overridden.

        Unchanged fields are copied from the current instance.
        This is a thin wrapper around dataclasses.replace to enforce the
        immutability contract at call sites.

        Example::

            new_state = state.evolve(iteration=state.iteration + 1, stop_reason="end_turn")
        """
        return dataclasses.replace(self, **changes)  # type: ignore[arg-type]
