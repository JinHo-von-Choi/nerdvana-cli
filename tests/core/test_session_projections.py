"""Tests for the event-sourced session log and its projections (Task 1.3).

Author: 최진호
Date:   2026-10-21
"""

from __future__ import annotations

from pathlib import Path

import pytest

from nerdvana_cli.core.state.projections import (
    CompactionLockProjection,
    ProjectionRegistry,
    SessionEventRecord,
    TurnBoundaryProjection,
    default_registry,
)
from nerdvana_cli.core.state.session import SessionStorage, messages_from_transcript

# ---------------------------------------------------------------------------
# The projections
# ---------------------------------------------------------------------------


def test_a_turn_opens_and_completes() -> None:
    registry = default_registry()

    registry.commit(SessionEventRecord(1, "turn/start", {"turn": 1}))
    assert registry.state_of("turn_boundary")["open_turn"] == 1
    assert registry.state_of("turn_boundary")["completed_turns"] == 0

    registry.commit(SessionEventRecord(2, "turn/end", {"turn": 1}))
    assert registry.state_of("turn_boundary")["open_turn"] is None
    assert registry.state_of("turn_boundary")["completed_turns"] == 1


def test_a_failed_attempt_is_counted_but_never_completes_a_turn() -> None:
    registry = default_registry()

    registry.commit(SessionEventRecord(1, "turn/start", {"turn": 1}))
    registry.commit(SessionEventRecord(2, "assistant/attempt", {"error": "stream broke"}))

    boundary = registry.state_of("turn_boundary")
    assert boundary["failed_attempts"] == 1
    assert boundary["completed_turns"] == 0
    assert boundary["open_turn"] == 1


def test_a_compaction_that_never_ends_leaves_an_orphan_lock() -> None:
    registry = default_registry()

    registry.commit(SessionEventRecord(1, "compaction/start", {"turn": 1}))
    assert registry.state_of("compaction_lock")["orphaned"] is True

    registry.commit(SessionEventRecord(2, "compaction/summary", {"shadowed_seqs": 42}))
    assert registry.state_of("compaction_lock")["shadowed_seqs"] == 42
    assert registry.state_of("compaction_lock")["orphaned"] is True

    registry.commit(SessionEventRecord(3, "compaction/end", {}))
    lock = registry.state_of("compaction_lock")
    assert lock["orphaned"] is False
    assert lock["compaction_count"] == 1


def test_a_failed_compaction_does_not_count_as_one() -> None:
    registry = default_registry()

    registry.commit(SessionEventRecord(1, "compaction/start", {}))
    registry.commit(SessionEventRecord(2, "compaction/end", {"error": "provider refused"}))

    lock = registry.state_of("compaction_lock")
    assert lock["orphaned"] is False
    assert lock["compaction_count"] == 0


def test_a_snapshot_reads_every_unit_at_one_watermark() -> None:
    registry = default_registry()

    registry.commit(SessionEventRecord(1, "turn/start", {"turn": 1}))
    registry.commit(SessionEventRecord(2, "compaction/start", {"turn": 1}))

    snapshot = registry.snapshot()

    assert snapshot.as_of_seq == 2
    assert snapshot.values["turn_boundary"]["open_turn"] == 1
    assert snapshot.values["compaction_lock"]["orphaned"] is True


def test_the_registry_refuses_an_out_of_order_event() -> None:
    registry = default_registry()
    registry.commit(SessionEventRecord(5, "turn/start", {"turn": 1}))

    with pytest.raises(ValueError):
        registry.commit(SessionEventRecord(5, "turn/end", {"turn": 1}))


def test_the_registry_refuses_two_units_under_one_key() -> None:
    registry = ProjectionRegistry()
    registry.register(TurnBoundaryProjection())

    with pytest.raises(ValueError):
        registry.register(TurnBoundaryProjection())


def test_an_unknown_key_is_not_read() -> None:
    with pytest.raises(KeyError):
        default_registry().state_of("nope")


def test_a_projection_is_pure() -> None:
    projection = TurnBoundaryProjection()
    state      = projection.init()

    first  = projection.apply(state, SessionEventRecord(1, "turn/start", {"turn": 7}))
    second = projection.apply(state, SessionEventRecord(1, "turn/start", {"turn": 7}))

    assert first == second
    assert state["open_turn"] is None      # the state it was given is untouched
    assert projection.view(first)["open_turn"] == 7


def test_the_compaction_lock_projection_starts_clean() -> None:
    assert CompactionLockProjection().init() == {
        "orphaned": False, "active_turn": None, "compaction_count": 0, "shadowed_seqs": 0,
    }


# ---------------------------------------------------------------------------
# The session log
# ---------------------------------------------------------------------------


def test_a_failed_attempt_is_recorded_but_never_reaches_the_model(tmp_path: Path) -> None:
    session = SessionStorage(session_id="s1", storage_dir=str(tmp_path))

    session.record_user_message("hello")
    session.record_assistant_attempt(error="stream broke", kind="overloaded", action="retry")
    session.record_assistant_message("hi there")

    entries = session.replay()
    assert any(e["type"] == "assistant_attempt" for e in entries)

    messages = messages_from_transcript(entries)
    assert [m.content for m in messages] == ["hello", "hi there"]
    assert not any("stream broke" in m.content for m in messages)


def test_the_compaction_bracket_is_written_in_order(tmp_path: Path) -> None:
    session = SessionStorage(session_id="s2", storage_dir=str(tmp_path))

    session.begin_compaction(tokens_before=9000, messages_before=40)
    session.record_compaction(tokens_before=9000, messages_before=40, strategy="ai", shadowed=35)
    session.end_compaction()

    phases = [e["phase"] for e in session.replay() if e["type"] == "compaction"]
    assert phases == ["start", "summary", "end"]
    assert session.orphaned_compaction_lock() is False
    assert session.projections.state_of("compaction_lock")["compaction_count"] == 1


def test_a_crash_during_a_compaction_leaves_an_orphan_lock(tmp_path: Path) -> None:
    session = SessionStorage(session_id="s3", storage_dir=str(tmp_path))

    session.begin_compaction(tokens_before=9000, messages_before=40)

    assert session.orphaned_compaction_lock() is True
    assert session.projections.state_of("compaction_lock")["active_turn"] == 0


def test_the_lock_is_found_again_after_the_session_is_read_back(tmp_path: Path) -> None:
    session = SessionStorage(session_id="s4", storage_dir=str(tmp_path))
    session.begin_compaction(tokens_before=9000, messages_before=40)

    reopened = SessionStorage(session_id="s4", storage_dir=str(tmp_path))
    reopened.load_messages()

    assert reopened.orphaned_compaction_lock() is True
    assert reopened.replay_projections().state_of("compaction_lock")["orphaned"] is True


def test_a_finished_compaction_leaves_no_orphan_after_a_reread(tmp_path: Path) -> None:
    session = SessionStorage(session_id="s5", storage_dir=str(tmp_path))
    session.begin_compaction(tokens_before=9000, messages_before=40)
    session.record_compaction(tokens_before=9000, messages_before=40, strategy="naive", shadowed=10)
    session.end_compaction()

    reopened = SessionStorage(session_id="s5", storage_dir=str(tmp_path))

    assert reopened.orphaned_compaction_lock() is False
    assert reopened.replay_projections().state_of("compaction_lock")["shadowed_seqs"] == 10


def test_the_compaction_bracket_closes_even_when_the_work_fails(tmp_path: Path) -> None:
    session = SessionStorage(session_id="s8", storage_dir=str(tmp_path))

    with pytest.raises(RuntimeError), session.compaction(tokens_before=9000, messages_before=40):
        raise RuntimeError("the provider died")

    assert session.orphaned_compaction_lock() is False
    assert [e["phase"] for e in session.replay() if e["type"] == "compaction"] == ["start", "end"]


def test_the_projection_of_a_session_follows_its_turns(tmp_path: Path) -> None:
    session = SessionStorage(session_id="s6", storage_dir=str(tmp_path))

    session.record_user_message("first")
    session.record_assistant_message("answer")
    session.record_user_message("second")
    session.record_assistant_message("another answer")

    boundary = session.projections.state_of("turn_boundary")
    assert boundary["completed_turns"] == 2
    assert boundary["open_turn"] is None
    assert session.projections.snapshot().as_of_seq == 4


def test_an_assistant_that_called_tools_does_not_end_its_turn(tmp_path: Path) -> None:
    session = SessionStorage(session_id="s7", storage_dir=str(tmp_path))

    session.record_user_message("look it up")
    session.record_assistant_message("", tool_uses=[{"id": "t1", "name": "Read", "input": {}}])
    session.record_tool_result("Read", "t1", "content")

    assert session.projections.state_of("turn_boundary")["open_turn"] == 1
    assert session.projections.state_of("turn_boundary")["completed_turns"] == 0
