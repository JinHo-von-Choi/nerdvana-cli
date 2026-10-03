"""The run store: atomic records, the lease that marks a run orphaned, idempotency keys and the task recorder.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from nerdvana_cli.core.state import run_store
from nerdvana_cli.core.state.run_store import (
    FAILED,
    ORPHANED,
    RUNNING,
    STOPPED,
    SUCCEEDED,
    TASK_KIND,
    RunRecord,
    RunStore,
    TaskRecorder,
    pid_alive,
    read_lines,
)


@pytest.fixture
def store(tmp_path: Path) -> RunStore:
    return RunStore(tmp_path / "runs")


def _record(run_id: str = "run_a", **values: object) -> RunRecord:
    now = time.time()
    fields: dict[str, object] = {"id": run_id, "kind": "session", "prompt": "p", "cwd": "/w", "started_at": now, "heartbeat_at": now, "owner_pid": 0}
    fields.update(values)
    return RunRecord(**fields)  # type: ignore[arg-type]


def _dead_pid() -> int:
    done = subprocess.Popen([sys.executable, "-c", "pass"])
    done.wait()
    return done.pid


# ---------------------------------------------------------------------------
# Records
# ---------------------------------------------------------------------------


def test_a_record_round_trips_and_a_write_leaves_no_temporary_file(store: RunStore) -> None:
    record = _record(worktree_path="/tmp/wt", prompt="fix the bug", cost_usd=0.5)
    store.save(record)
    assert store.load("run_a") == record
    assert [path.name for path in store.run_dir("run_a").iterdir()] == ["record.json"]


def test_unknown_fields_in_a_saved_record_are_ignored_and_a_broken_file_reads_as_missing(store: RunStore) -> None:
    store.save(_record())
    path = store.run_dir("run_a") / "record.json"
    data = json.loads(path.read_text())
    path.write_text(json.dumps({**data, "added_later": 1}))
    assert store.load("run_a") is not None
    path.write_text("{ not json")
    assert store.load("run_a") is None
    assert store.load("never_existed") is None


def test_all_lists_the_newest_first_and_a_prefix_resolves_to_a_unique_run(store: RunStore) -> None:
    store.save(_record("run_aaa", started_at=100.0))
    store.save(_record("run_bbb", started_at=200.0))
    assert [r.id for r in store.all()] == ["run_bbb", "run_aaa"]
    assert store.resolve("run_a").id == "run_aaa"  # type: ignore[union-attr]
    assert store.resolve("run_") is None


def test_update_changes_only_the_named_fields_and_reports_a_missing_run(store: RunStore) -> None:
    store.save(_record(prompt="keep"))
    updated = store.update("run_a", status=FAILED, error="boom")
    assert updated is not None and updated.status == FAILED and updated.prompt == "keep"
    assert store.update("nobody", status=FAILED) is None


def test_read_lines_returns_only_complete_lines_and_resumes_from_the_offset(tmp_path: Path) -> None:
    log = tmp_path / "log"
    assert read_lines(log) == ([], 0)
    log.write_bytes(b"one\ntwo\npart")
    lines, offset = read_lines(log)
    assert lines == ["one", "two"] and offset == 8
    log.write_bytes(b"one\ntwo\npartial\nthree\n")
    assert read_lines(log, offset)[0] == ["partial", "three"]


# ---------------------------------------------------------------------------
# The lease
# ---------------------------------------------------------------------------


def test_a_run_with_a_stale_heartbeat_and_no_process_is_orphaned(store: RunStore) -> None:
    record = _record(owner_pid=_dead_pid(), heartbeat_at=time.time() - run_store.LEASE_SECONDS - 1)
    assert store.effective_status(record) == ORPHANED


def test_a_live_owner_or_a_fresh_heartbeat_keeps_a_run_running(store: RunStore) -> None:
    stale = time.time() - run_store.LEASE_SECONDS - 1
    assert store.effective_status(_record(owner_pid=os.getpid(), heartbeat_at=stale)) == RUNNING
    assert store.effective_status(_record(owner_pid=_dead_pid(), heartbeat_at=time.time())) == RUNNING


def test_a_finished_run_keeps_its_status_whatever_its_heartbeat_says(store: RunStore) -> None:
    for status in (SUCCEEDED, FAILED, STOPPED):
        assert store.effective_status(_record(status=status, owner_pid=_dead_pid(), heartbeat_at=0.0)) == status


def test_pid_alive() -> None:
    assert pid_alive(os.getpid())
    assert not pid_alive(_dead_pid())
    assert not pid_alive(0)


def test_touch_renews_the_lease(store: RunStore) -> None:
    store.save(_record(heartbeat_at=1.0))
    store.touch("run_a")
    assert store.load("run_a").heartbeat_at > time.time() - 5  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# Idempotency keys
# ---------------------------------------------------------------------------


def test_a_second_record_with_the_same_key_returns_the_first(store: RunStore) -> None:
    first, created = store.create(_record("run_one", key="nightly"))
    second, again  = store.create(_record("run_two", key="nightly"))
    assert created and not again
    assert second.id == "run_one" and first.id == "run_one"
    assert store.load("run_two") is None
    assert [r.id for r in store.all()] == ["run_one"]


def test_records_without_a_key_never_collide(store: RunStore) -> None:
    assert store.create(_record("run_one"))[1] and store.create(_record("run_two"))[1]


def test_simultaneous_creations_with_one_key_leave_exactly_one_winner(store: RunStore) -> None:
    results: list[tuple[str, bool]] = []
    barrier = threading.Barrier(8)

    def attempt(index: int) -> None:
        barrier.wait()
        record, created = store.create(_record(f"run_{index}", key="same"))
        results.append((record.id, created))

    threads = [threading.Thread(target=attempt, args=(i,)) for i in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    winners = [rid for rid, created in results if created]
    assert len(winners) == 1
    assert {rid for rid, _ in results} == set(winners)
    assert [r.id for r in store.all()] == winners


def test_removing_a_record_frees_its_key(store: RunStore) -> None:
    first, _ = store.create(_record("run_one", key="k"))
    store.remove(first)
    again, created = store.create(_record("run_two", key="k"))
    assert created and again.id == "run_two"


# ---------------------------------------------------------------------------
# The task recorder
# ---------------------------------------------------------------------------


def test_a_background_task_is_recorded_when_it_starts_and_closed_when_it_ends(store: RunStore) -> None:
    recorder = TaskRecorder.start("agent_ab12", "survey the repo", "/w", store=store)
    assert recorder is not None
    saved = store.load("agent_ab12")
    assert saved is not None and saved.kind == TASK_KIND and saved.status == RUNNING and saved.session_id == "agent_ab12"
    recorder.finish(SUCCEEDED, "the survey", "", 0.25)
    done = store.load("agent_ab12")
    assert done is not None and done.status == SUCCEEDED and done.cost_usd == 0.25 and done.finished_at is not None
    assert Path(done.result_path).read_text() == "the survey"


async def test_the_lease_is_renewed_while_the_task_runs(store: RunStore, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(run_store, "HEARTBEAT_SECONDS", 0.05)
    recorder = TaskRecorder.start("agent_beat", "p", "/w", store=store)
    assert recorder is not None
    store.update("agent_beat", heartbeat_at=1.0)
    beating = asyncio.ensure_future(recorder.keep_lease())
    await asyncio.sleep(0.3)
    beating.cancel()
    assert store.load("agent_beat").heartbeat_at > time.time() - 5  # type: ignore[union-attr]


def test_a_store_that_cannot_be_written_does_not_stop_the_task(tmp_path: Path) -> None:
    blocked = tmp_path / "file"
    blocked.write_text("not a directory")
    assert TaskRecorder.start("agent_x", "p", "/w", store=RunStore(blocked / "runs")) is None
