"""`nerdvana agents ...` through the real Typer app, with a fake nerdvana command behind the supervisor.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest
from typer.testing import CliRunner

from nerdvana_cli.cli.supervisor import COMMAND_ENV
from nerdvana_cli.commands.agents_command import format_age, render_event
from nerdvana_cli.core.state.run_store import SUCCEEDED, RunRecord, RunStore
from nerdvana_cli.main import app

FAKE = textwrap.dedent('''
    import json, sys, time
    prompt = sys.argv[-1]
    def emit(event):
        print(json.dumps(event), flush=True)
    emit({"type": "system", "subtype": "init", "session_id": "fake-session"})
    emit({"type": "tool_start", "name": "Bash", "summary": "pytest -q"})
    emit({"type": "tool_done", "name": "Bash", "is_error": False})
    emit({"type": "request", "cost_usd": 0.02})
    while "slow" in prompt:
        time.sleep(0.1)
    time.sleep(0.3)
    emit({"type": "text", "text": "tests pass"})
    emit({"type": "result", "subtype": "success", "result": "tests pass", "total_cost_usd": 0.02})
''')


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    script = tmp_path / "fake_nerdvana.py"
    script.write_text(FAKE, encoding="utf-8")
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.setenv("NERDVANA_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv(COMMAND_ENV, f"{sys.executable} {script}")
    monkeypatch.chdir(work)
    return work


def _invoke(*args: str):  # type: ignore[no-untyped-def]
    return CliRunner().invoke(app, ["--no-update-check", "agents", *args])


def _only_run() -> RunRecord:
    (record,) = RunStore().all()
    return record


def _wait_done(run_id: str, timeout: float = 20.0) -> RunRecord:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        record = RunStore().load(run_id)
        if record is not None and record.status != "running":
            return record
        time.sleep(0.05)
    raise AssertionError("the run did not end")


def test_start_prints_the_id_and_the_run_goes_on_without_the_command(env: Path) -> None:
    result = _invoke("start", "fix the tests", "--max-cost-usd", "1")
    assert result.exit_code == 0, result.output
    record = _only_run()
    assert f"Started run {record.id}" in result.output
    assert record.prompt == "fix the tests" and record.max_cost_usd == 1.0 and record.cwd == str(env.resolve())
    assert _wait_done(record.id).status == SUCCEEDED


def test_start_with_a_key_twice_says_so_and_runs_once(env: Path) -> None:
    first  = _invoke("start", "nightly job", "--key", "k1")
    second = _invoke("start", "nightly job", "--key", "k1")
    assert "Started run" in first.output and "Already started run" in second.output
    assert len(RunStore().all()) == 1
    _wait_done(_only_run().id)


def test_start_rejects_a_directory_that_does_not_exist_and_a_worktree_outside_git(env: Path, tmp_path: Path) -> None:
    assert _invoke("start", "p", "--cwd", str(tmp_path / "missing")).exit_code == 2
    result = _invoke("start", "p", "--worktree")
    assert result.exit_code == 1 and "git repository" in result.output


def test_list_shows_id_status_age_cost_and_last_signal(env: Path) -> None:
    assert "No runs." in _invoke("list").output
    _invoke("start", "list me")
    record = _wait_done(_only_run().id)
    output = _invoke("list").output
    assert record.id in output and "succeeded" in output and "$0.0200" in output and "result success" in output


def test_list_marks_a_run_whose_process_is_gone_as_orphaned(env: Path) -> None:
    dead = subprocess.Popen([sys.executable, "-c", "pass"])
    dead.wait()
    now = time.time()
    RunStore().save(RunRecord(id="run_lost", kind="session", prompt="p", cwd="/w", started_at=now - 3600, heartbeat_at=now - 600, owner_pid=dead.pid))
    output = _invoke("list").output
    assert "orphaned" in output and "agents resume" in output and "1h" in output


def test_show_prints_the_record_the_log_tail_and_the_result(env: Path) -> None:
    _invoke("start", "show me")
    record = _wait_done(_only_run().id)
    output = _invoke("show", record.id[:7], "--lines", "5").output
    assert record.id in output and "succeeded" in output and "fake-session" in output
    assert "> Bash pytest -q" in output and "tests pass" in output
    assert '"subtype": "success"' in output


def test_show_names_an_unknown_run(env: Path) -> None:
    result = _invoke("show", "run_nope")
    assert result.exit_code == 1 and "no run" in result.output


def test_attach_follows_the_log_until_the_run_ends(env: Path) -> None:
    _invoke("start", "follow me")
    record = _only_run()
    result = _invoke("attach", record.id)
    assert result.exit_code == 0, result.output
    assert "> Bash pytest -q" in result.output and "tests pass" in result.output and "[result: success" in result.output
    assert f"Run {record.id} succeeded" in result.output


def test_stop_ends_a_running_run(env: Path) -> None:
    _invoke("start", "slow work")
    record = _only_run()
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline and not (RunStore().load(record.id) or record).child_pid:
        time.sleep(0.05)
    result = _invoke("stop", record.id)
    assert result.exit_code == 0 and f"Run {record.id} stopped" in result.output
    again = _invoke("stop", record.id)
    assert again.exit_code == 1 and "already ended" in again.output


def test_resume_says_why_a_run_cannot_continue(env: Path) -> None:
    _invoke("start", "slow work")
    record = _only_run()
    running = _invoke("resume", record.id)
    assert running.exit_code == 1 and "running" in running.output
    _invoke("stop", record.id)
    stopped = _invoke("resume", record.id)
    assert stopped.exit_code == 1 and "no session transcript" in stopped.output


def test_clean_removes_old_finished_runs_and_reports_how_many(env: Path) -> None:
    _invoke("start", "clean me")
    record = _wait_done(_only_run().id)
    assert "Removed 0 run(s)" in _invoke("clean").output
    result = _invoke("clean", "--days", "0")
    assert "Removed 1 run(s)" in result.output
    assert RunStore().load(record.id) is None


def test_age_and_event_rendering() -> None:
    assert [format_age(s) for s in (4, 90, 7300, 200000)] == ["4s", "1m", "2h", "2d"]
    assert render_event('{"type": "tool_start", "name": "Edit", "summary": "a.py"}') == "> Edit a.py"
    assert render_event('{"type": "text", "text": "hi"}') == "hi"
    assert render_event('{"type": "request", "cost_usd": 1}') == ""
    assert render_event("not json") == "not json"
