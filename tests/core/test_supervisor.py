"""The background supervisor: detached runs, the monitor's lease and log reading, stop, resume and clean.

A fake ``nerdvana`` command stands in for the real one (``NERDVANA_AGENTS_COMMAND``); nothing calls a model.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from nerdvana_cli.cli import supervisor
from nerdvana_cli.cli.supervisor import (
    COMMAND_ENV,
    RESUME_PROMPT,
    LogScan,
    SupervisorError,
    build_run_command,
    clean_runs,
    monitor,
    resume_run,
    start_run,
    stop_run,
)
from nerdvana_cli.core import cancellation, run_store
from nerdvana_cli.core.run_store import FAILED, ORPHANED, RUNNING, STOPPED, SUCCEEDED, RunRecord, RunStore, pid_alive
from nerdvana_cli.core.session import SessionStorage

FAKE = textwrap.dedent('''
    import json, os, signal, sys, time
    args = sys.argv[1:]
    prompt = args[-1]
    if os.environ.get("FAKE_LOG"):
        with open(os.environ["FAKE_LOG"], "a") as handle:
            handle.write(json.dumps({"args": args, "cwd": os.getcwd(), "pid": os.getpid()}) + "\\n")
    if "stubborn" in prompt:
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
    session = args[args.index("--resume") + 1] if "--resume" in args else "fake-session"
    def emit(event):
        print(json.dumps(event), flush=True)
    emit({"type": "system", "subtype": "init", "session_id": session})
    if "edit" in prompt:
        open("edited.txt", "w").write("changed\\n")
    if "crash" in prompt:
        sys.stderr.write("boom\\n")
        sys.exit(3)
    for _ in range(int(os.environ.get("FAKE_STEPS", "2"))):
        emit({"type": "tool_start", "name": "Bash", "summary": "ls"})
        emit({"type": "request", "cost_usd": 0.01})
        time.sleep(float(os.environ.get("FAKE_PAUSE", "0.05")))
    while "slow" in prompt or "stubborn" in prompt:
        time.sleep(0.1)
    emit({"type": "text", "text": "all done"})
    emit({"type": "result", "subtype": "success", "result": "all done", "total_cost_usd": 0.5, "session_id": session})
''')


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Data root and the fake command under tmp_path; returns the work directory."""
    script = tmp_path / "fake_nerdvana.py"
    script.write_text(FAKE, encoding="utf-8")
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.setenv("NERDVANA_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv(COMMAND_ENV, f"{sys.executable} {script}")
    monkeypatch.setenv("FAKE_LOG", str(tmp_path / "calls.jsonl"))
    for name in ("FAKE_STEPS", "FAKE_PAUSE"):
        monkeypatch.delenv(name, raising=False)
    return work


def _calls(tmp_path: Path) -> list[dict[str, object]]:
    log = tmp_path / "calls.jsonl"
    return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []


def _wait_for(store: RunStore, run_id: str, done, timeout: float = 20.0) -> RunRecord:  # type: ignore[no-untyped-def]
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        record = store.load(run_id)
        if record is not None and done(record):
            return record
        time.sleep(0.05)
    raise AssertionError(f"run {run_id} did not get there: {store.load(run_id)}")


def _record(store: RunStore, run_id: str = "run_t", **values: object) -> RunRecord:
    now = time.time()
    fields: dict[str, object] = {"id": run_id, "kind": "session", "prompt": "do it", "cwd": "/w", "started_at": now, "heartbeat_at": now, "owner_pid": 0}
    fields.update(values)
    record = RunRecord(**fields)  # type: ignore[arg-type]
    store.save(record)
    return record


def _dead_pid() -> int:
    done = subprocess.Popen([sys.executable, "-c", "pass"])
    done.wait()
    return done.pid


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@t", *args], check=True, capture_output=True)


# ---------------------------------------------------------------------------
# Command line and log scan
# ---------------------------------------------------------------------------


def test_the_run_command_carries_the_directory_the_ceiling_and_the_prompt(env: Path) -> None:
    store = RunStore()
    record = _record(store, cwd=str(env), prompt="fix it", max_cost_usd=0.5, approval_mode="auto_edit")
    command = build_run_command(record)
    assert command[-2:] == ["--", "fix it"]
    tail = command[command.index("run"):]
    assert tail[:5] == ["run", "--cwd", str(env), "--output-format", "stream-json"]
    assert tail[5:7] == ["--approval-mode", "auto_edit"]
    assert "--max-cost-usd" in tail and "0.5" in tail and "--require-price" in tail


def test_a_resumed_run_continues_the_recorded_session(env: Path) -> None:
    command = build_run_command(_record(RunStore(), resume_session="sess-1"))
    assert command[-4:] == ["--resume", "sess-1", "--", RESUME_PROMPT]


def test_the_agents_command_comes_from_the_environment_or_this_interpreter(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(COMMAND_ENV, "fake-nerdvana --flag")
    assert supervisor.agents_command() == ["fake-nerdvana", "--flag"]
    monkeypatch.delenv(COMMAND_ENV)
    assert supervisor.agents_command()[-2:] == ["-m", "nerdvana_cli.main"]


def test_the_log_scan_learns_the_session_the_cost_and_the_last_event(tmp_path: Path) -> None:
    log = tmp_path / "run.log"
    log.write_text(
        '{"type": "system", "subtype": "init", "session_id": "s-9"}\n'
        '{"type": "request", "cost_usd": 0.25}\n{"type": "request", "cost_usd": 0.5}\n'
        'not json\n{"type": "tool_start", "name": "Edit", "summary": "a.py"}\n{"type": "context", "percent": 4}\n'
        '{"type": "request", "cost_usd": 0.25',
        encoding="utf-8",
    )
    scan = LogScan()
    scan.read(log)
    assert (scan.session_id, scan.cost_usd, scan.last_signal, scan.result) == ("s-9", 0.75, "tool Edit", None)
    with log.open("a") as handle:
        handle.write('}\n{"type": "result", "subtype": "success", "total_cost_usd": 1.0}\n')
    scan.read(log)
    assert scan.cost_usd == 1.0 and scan.result is not None and scan.last_signal == "result"


# ---------------------------------------------------------------------------
# The monitor, in this process
# ---------------------------------------------------------------------------


async def test_a_run_ends_succeeded_with_its_result_session_and_cost(env: Path, tmp_path: Path) -> None:
    store = RunStore()
    _record(store, cwd=str(env), prompt="hello")
    assert await monitor("run_t", store) == 0
    done = store.load("run_t")
    assert done is not None and done.status == SUCCEEDED and done.exit_code == 0
    assert done.session_id == "fake-session" and done.cost_usd == 0.5 and done.last_signal == "result success"
    assert json.loads(Path(done.result_path).read_text())["result"] == "all done"
    assert '"type": "text"' in (store.run_dir("run_t") / "run.log").read_text()
    (call,) = _calls(tmp_path)
    assert call["cwd"] == str(env) and call["args"][-1] == "hello"  # type: ignore[index]


async def test_a_run_that_exits_without_a_result_ends_failed_with_the_reason(env: Path) -> None:
    store = RunStore()
    _record(store, cwd=str(env), prompt="crash please")
    await monitor("run_t", store)
    done = store.load("run_t")
    assert done is not None and done.status == FAILED and done.exit_code == 3
    assert "exit code 3" in done.error and "boom" in (store.run_dir("run_t") / "stderr.log").read_text()
    assert json.loads(Path(done.result_path).read_text())["is_error"] is True


async def test_a_command_that_cannot_start_ends_the_run_failed(env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(COMMAND_ENV, "/nonexistent/nerdvana-binary")
    store = RunStore()
    _record(store, cwd=str(env))
    assert await monitor("run_t", store) == 1
    done = store.load("run_t")
    assert done is not None and done.status == FAILED and "nerdvana-binary" in done.error


async def test_the_monitor_renews_the_lease_and_reports_progress_while_the_run_goes_on(env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(run_store, "HEARTBEAT_SECONDS", 0.1)
    monkeypatch.setattr(supervisor, "_TICK_SECONDS", 0.05)
    monkeypatch.setenv("FAKE_STEPS", "3")
    store = RunStore()
    _record(store, cwd=str(env), prompt="slow task", heartbeat_at=1.0)
    running = asyncio.create_task(monitor("run_t", store))
    deadline = time.monotonic() + 10
    seen = None
    while time.monotonic() < deadline:
        seen = store.load("run_t")
        if seen and seen.heartbeat_at > time.time() - 3 and seen.last_signal and seen.cost_usd > 0:
            break
        await asyncio.sleep(0.05)
    assert seen is not None and seen.status == RUNNING and seen.owner_pid == os.getpid() and seen.session_id == "fake-session"
    assert seen.child_pid > 0 and pid_alive(seen.child_pid) and seen.cost_usd >= 0.01
    os.kill(os.getpid(), signal.SIGTERM)
    assert await asyncio.wait_for(running, 10) == 0
    done = store.load("run_t")
    assert done is not None and done.status == STOPPED
    assert not pid_alive(done.child_pid)


async def test_a_run_stopped_before_its_monitor_started_never_starts(env: Path, tmp_path: Path) -> None:
    store = RunStore()
    _record(store, cwd=str(env), prompt="slow task", status=STOPPED, finished_at=time.time())
    assert await monitor("run_t", store) == 0
    assert _calls(tmp_path) == []
    assert store.load("run_t").status == STOPPED  # type: ignore[union-attr]


async def test_a_record_that_stops_saying_running_ends_the_run_at_the_next_beat(env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(run_store, "HEARTBEAT_SECONDS", 0.1)
    monkeypatch.setattr(supervisor, "_TICK_SECONDS", 0.05)
    store = RunStore()
    _record(store, cwd=str(env), prompt="slow task")
    running = asyncio.create_task(monitor("run_t", store))
    record = None
    for _ in range(200):
        record = store.load("run_t")
        if record and record.child_pid:
            break
        await asyncio.sleep(0.05)
    assert record is not None and record.child_pid
    store.update("run_t", status=STOPPED)
    assert await asyncio.wait_for(running, 10) == 0
    assert not pid_alive(record.child_pid)


async def test_a_stop_kills_a_run_that_ignores_sigterm_after_the_grace_period(env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cancellation, "CANCEL_GRACE_SECONDS", 0.5)
    monkeypatch.setattr(supervisor, "_TICK_SECONDS", 0.05)
    store = RunStore()
    _record(store, cwd=str(env), prompt="stubborn task")
    running = asyncio.create_task(monitor("run_t", store))
    record = None
    for _ in range(200):
        record = store.load("run_t")
        if record and record.child_pid and record.session_id:
            break
        await asyncio.sleep(0.05)
    assert record is not None and record.child_pid
    started = time.monotonic()
    os.kill(os.getpid(), signal.SIGTERM)
    await asyncio.wait_for(running, 10)
    assert time.monotonic() - started < 5
    assert store.load("run_t").status == STOPPED  # type: ignore[union-attr]
    assert not pid_alive(record.child_pid)


# ---------------------------------------------------------------------------
# start, idempotency, stop, orphans, resume, clean: the real detached monitor
# ---------------------------------------------------------------------------


def test_start_runs_the_prompt_detached_and_records_it(env: Path, tmp_path: Path) -> None:
    record, started = start_run("summarize the repo", str(env), max_cost_usd=0.5)
    assert started and record.kind == "session" and record.status == RUNNING
    store = RunStore()
    done  = _wait_for(store, record.id, lambda r: r.status != RUNNING)
    assert done.status == SUCCEEDED and done.owner_pid > 0 and done.attempts == 1
    assert done.log_path == str(store.run_dir(record.id) / "run.log") and Path(done.log_path).is_file()
    (call,) = _calls(tmp_path)
    assert call["args"][-1] == "summarize the repo" and "--require-price" in call["args"]  # type: ignore[operator]


def test_starting_again_with_the_same_key_returns_the_existing_run_and_starts_nothing(env: Path, tmp_path: Path) -> None:
    first, started = start_run("once", str(env), key="nightly")
    second, again  = start_run("once", str(env), key="nightly")
    assert started and not again and second.id == first.id
    _wait_for(RunStore(), first.id, lambda r: r.status != RUNNING)
    assert len(_calls(tmp_path)) == 1
    assert len(RunStore().all()) == 1


def test_a_run_in_a_worktree_works_there_and_keeps_it_when_it_changed_something(env: Path, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    (repo / "a.txt").write_text("one\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    changed,   _ = start_run("edit a file", str(repo), worktree=True)
    unchanged, _ = start_run("look around", str(repo), worktree=True)
    store = RunStore()
    for record in (changed, unchanged):
        _wait_for(store, record.id, lambda r: r.status != RUNNING)
    changed_done   = store.load(changed.id)
    unchanged_done = store.load(unchanged.id)
    assert changed_done is not None and unchanged_done is not None
    assert Path(changed_done.worktree_path, "edited.txt").is_file() and changed_done.worktree_branch.startswith("nerdvana/")
    assert {call["cwd"] for call in _calls(tmp_path)} == {changed_done.worktree_path, unchanged_done.worktree_path}

    cleaned = {item.run_id: item.kept_worktree for item in clean_runs(0, store)}
    assert cleaned == {changed.id: changed_done.worktree_path, unchanged.id: ""}
    assert Path(changed_done.worktree_path).is_dir() and not Path(unchanged_done.worktree_path).exists()
    assert store.all() == []


def test_a_worktree_outside_a_git_repository_is_refused_and_leaves_a_failed_record(env: Path) -> None:
    with pytest.raises(SupervisorError, match="git repository"):
        start_run("anything", str(env), worktree=True)
    (record,) = RunStore().all()
    assert record.status == FAILED and "git repository" in record.error


def test_stop_ends_a_running_run_gracefully(env: Path) -> None:
    record, _ = start_run("slow job", str(env))
    store = RunStore()
    running = _wait_for(store, record.id, lambda r: r.child_pid > 0 and bool(r.session_id))
    stopped = stop_run(record.id[:8])
    assert stopped.status == STOPPED
    assert not pid_alive(running.child_pid) and not pid_alive(running.owner_pid)


def test_stop_refuses_a_finished_run_and_a_live_background_task(env: Path) -> None:
    store = RunStore()
    _record(store, "run_done", status=SUCCEEDED)
    with pytest.raises(SupervisorError, match="already ended"):
        stop_run("run_done", store)
    _record(store, "task_live", kind="task", owner_pid=os.getpid())
    with pytest.raises(SupervisorError, match="TaskStop"):
        stop_run("task_live", store)
    with pytest.raises(SupervisorError, match="no run"):
        stop_run("nothing", store)


def test_stopping_an_orphaned_run_marks_it_stopped_and_ends_a_process_it_left_behind(env: Path) -> None:
    store  = RunStore()
    leftover = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], start_new_session=True)
    _record(store, owner_pid=_dead_pid(), child_pid=leftover.pid, heartbeat_at=time.time() - 60)
    assert store.effective_status(store.load("run_t")) == ORPHANED  # type: ignore[arg-type]
    assert stop_run("run_t", store).status == STOPPED
    leftover.wait(timeout=10)
    assert leftover.returncode is not None


def test_an_orphaned_run_resumes_from_its_session_transcript(env: Path, tmp_path: Path) -> None:
    transcript = SessionStorage(session_id="sess-resume")
    transcript.record_user_message("start the task")
    transcript.record_assistant_message("working on it")
    store = RunStore()
    _record(store, cwd=str(env), session_id="sess-resume", owner_pid=_dead_pid(), heartbeat_at=time.time() - 60)

    resumed = resume_run("run_t", store)
    assert resumed.attempts == 2 and resumed.resume_session == "sess-resume"
    done = _wait_for(store, "run_t", lambda r: r.status != RUNNING)
    assert done.status == SUCCEEDED and done.session_id == "sess-resume"
    (call,) = _calls(tmp_path)
    assert call["args"][-4:] == ["--resume", "sess-resume", "--", RESUME_PROMPT]  # type: ignore[index]


def test_a_run_without_a_transcript_cannot_be_resumed(env: Path) -> None:
    store = RunStore()
    _record(store, "run_none", owner_pid=_dead_pid(), heartbeat_at=time.time() - 60)
    with pytest.raises(SupervisorError, match="no session transcript"):
        resume_run("run_none", store)
    _record(store, "run_gone", session_id="never-recorded", owner_pid=_dead_pid(), heartbeat_at=time.time() - 60)
    with pytest.raises(SupervisorError, match="no session transcript"):
        resume_run("run_gone", store)


def test_only_an_abandoned_stopped_or_failed_run_can_be_resumed(env: Path) -> None:
    store = RunStore()
    SessionStorage(session_id="sess-x").record_user_message("hi")
    _record(store, "run_live", session_id="sess-x", owner_pid=os.getpid())
    _record(store, "run_ok", session_id="sess-x", status=SUCCEEDED)
    for run_id, status in (("run_live", "running"), ("run_ok", "succeeded")):
        with pytest.raises(SupervisorError, match=status):
            resume_run(run_id, store)


def test_a_background_task_of_a_dead_session_resumes_as_a_supervised_run(env: Path, tmp_path: Path) -> None:
    SessionStorage(session_id="agent_dead01").record_user_message("survey the repo")
    store = RunStore()
    _record(store, "agent_dead01", kind="task", cwd=str(env), session_id="agent_dead01", owner_pid=_dead_pid(), heartbeat_at=time.time() - 60)
    resumed = resume_run("agent_dead01", store)
    assert resumed.kind == "session"
    assert _wait_for(store, "agent_dead01", lambda r: r.status != RUNNING).status == SUCCEEDED


def test_clean_removes_only_finished_runs_older_than_the_limit_and_their_keys(env: Path) -> None:
    store = RunStore()
    now = time.time()
    _record(store, "run_old",    status=SUCCEEDED, finished_at=now - 10 * 86400, key="old")
    _record(store, "run_new",    status=FAILED,    finished_at=now - 3600)
    _record(store, "run_live",   owner_pid=os.getpid())
    _record(store, "run_orphan", owner_pid=_dead_pid(), heartbeat_at=now - 100)
    assert [item.run_id for item in clean_runs(7, store)] == ["run_old"]
    assert {r.id for r in store.all()} == {"run_new", "run_live", "run_orphan"}
    assert store.create(_record(store, "run_again", key="old", status=SUCCEEDED, finished_at=now))[1]
    assert {item.run_id for item in clean_runs(0, store)} == {"run_new", "run_again"}
    assert {r.id for r in store.all()} == {"run_live", "run_orphan"}
