"""Contract tests for T-0A-03: LoopState immutability and decomposition.

Verifies:
- LoopState.evolve() returns a new instance (immutability).
- Fields not specified in evolve() are preserved unchanged.
- Multiple sequential evolve() calls correctly accumulate changes.
"""

from __future__ import annotations

import pytest

from nerdvana_cli.core.loop_state import LoopState


@pytest.fixture
def base_state() -> LoopState:
    return LoopState(
        iteration         = 1,
        stop_reason       = "continue",
        continuation_hint = None,
        token_budget_used = 0,
        session_id        = "test-session-00",
    )


def test_evolve_returns_new_instance(base_state: LoopState) -> None:
    """evolve() must return a distinct object, not mutate the original."""
    new_state = base_state.evolve(iteration=2)

    assert new_state is not base_state, "evolve() must return a new LoopState instance"


def test_evolve_preserves_unchanged_fields(base_state: LoopState) -> None:
    """Fields not passed to evolve() must retain their original values."""
    new_state = base_state.evolve(iteration=5, stop_reason="end_turn")

    assert new_state.session_id        == base_state.session_id
    assert new_state.token_budget_used == base_state.token_budget_used
    assert new_state.continuation_hint == base_state.continuation_hint


def test_evolve_applies_changes(base_state: LoopState) -> None:
    """Fields passed to evolve() must reflect the new values."""
    new_state = base_state.evolve(
        iteration         = 3,
        stop_reason       = "max_tokens",
        continuation_hint = "resume from tool_use",
        token_budget_used = 8192,
    )

    assert new_state.iteration         == 3
    assert new_state.stop_reason       == "max_tokens"
    assert new_state.continuation_hint == "resume from tool_use"
    assert new_state.token_budget_used == 8192


def test_evolve_original_is_frozen(base_state: LoopState) -> None:
    """The original LoopState must remain unchanged after evolve()."""
    _ = base_state.evolve(iteration=99, stop_reason="tool_error")

    assert base_state.iteration   == 1
    assert base_state.stop_reason == "continue"


def test_sequential_evolve_is_independent(base_state: LoopState) -> None:
    """Sequential evolve() calls must each be independent of one another."""
    state_a = base_state.evolve(iteration=2)
    state_b = base_state.evolve(iteration=3)

    assert state_a.iteration == 2
    assert state_b.iteration == 3
    assert state_a is not state_b


def test_frozen_dataclass_rejects_direct_mutation(base_state: LoopState) -> None:
    """Assigning to a frozen dataclass field must raise FrozenInstanceError."""
    import dataclasses

    with pytest.raises(dataclasses.FrozenInstanceError):
        base_state.iteration = 99  # type: ignore[misc]
