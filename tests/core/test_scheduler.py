"""Scheduler: job store, lock, run records and the daemon tick, with a fake nerdvana command.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

import json
import os
import sys
import textwrap
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from nerdvana_cli.cli import scheduler
from nerdvana_cli.cli.cron import ScheduleError
from nerdvana_cli.cli.scheduler import (
    COMMAND_ENV,
    Job,
    JobLock,
    JobStore,
    LaunchError,
    Scheduler,
    build_run_command,
    finish_job,
    latest_record,
    poll_job,
    record_skipped,
    run_job_now,
    scheduler_command,
    serve,
    spent_on,
    start_job,
    systemd_unit,
    validate_job,
)
from nerdvana_cli.core import paths as core_paths

FAKE = textwrap.dedent('''
    import json, os, sys, time
    args = sys.argv[1:]
    if os.environ.get("FAKE_LOG"):
        with open(os.environ["FAKE_LOG"], "a") as handle:
            handle.write(json.dumps({"args": args, "cwd": os.getcwd()}) + "\\n")
    if os.environ.get("FAKE_SLEEP"):
        time.sleep(float(os.environ["FAKE_SLEEP"]))
    sys.stderr.write("fake stderr line\\n")
    if os.environ.get("FAKE_RAW"):
        print(os.environ["FAKE_RAW"])
    else:
        print(json.dumps({"type": "result", "result": "all done", "total_cost_usd": float(os.environ.get("FAKE_COST", "0.25"))}))
    sys.exit(int(os.environ.get("FAKE_EXIT", "0")))
''')

T0 = datetime(2026, 1, 5, 12, 0, 30)


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Data root and the fake nerdvana command, both under tmp_path; returns the work directory."""
    script = tmp_path / "fake_nerdvana.py"
    script.write_text(FAKE, encoding="utf-8")
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.setenv("NERDVANA_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv(COMMAND_ENV, f"{sys.executable} {script}")
    monkeypatch.setenv("FAKE_LOG", str(tmp_path / "calls.jsonl"))
    for name in ("FAKE_SLEEP", "FAKE_RAW", "FAKE_COST", "FAKE_EXIT"):
        monkeypatch.delenv(name, raising=False)
    return work


def _job(work: Path, name: str = "nightly", schedule: str = "every 1m", **overrides: object) -> Job:
    values: dict[str, object] = {"name": name, "schedule": schedule, "prompt": "summarize the repo", "cwd": str(work)}
    values.update(overrides)
    return Job(**values)  # type: ignore[arg-type]


def _calls(tmp_path: Path) -> list[dict[str, object]]:
    log = tmp_path / "calls.jsonl"
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()] if log.exists() else []


def _drain(scheduler_: Scheduler, timeout: float = 20.0) -> list[str]:
    """Wait until every started run has finished; returns the log lines."""
    lines: list[str] = []
    deadline = time.monotonic() + timeout
    while scheduler_.running and time.monotonic() < deadline:
        lines.extend(scheduler_.reap())
        time.sleep(0.05)
    assert not scheduler_.running
    return lines


class TestJobStore:
    def test_a_missing_file_is_an_empty_list(self, env: Path) -> None:
        assert JobStore().load() == []

    def test_the_file_lives_under_the_data_root(self, env: Path) -> None:
        JobStore().add(_job(env))
        assert core_paths.schedule_jobs_path() == core_paths.user_data_home() / "schedule" / "jobs.yml"
        assert core_paths.schedule_jobs_path().is_file()

    def test_jobs_round_trip_with_every_field(self, env: Path) -> None:
        job = _job(env, schedule="*/5 9-17 * * 1-5", max_cost_usd=0.5, approval_mode="default", created_at="2026-01-05T12:00:00")
        JobStore().add(job)
        assert JobStore().load() == [job]

    def test_defaults_are_read_only_mode_and_a_cost_ceiling(self, env: Path) -> None:
        JobStore().add(_job(env))
        loaded = JobStore().load()[0]
        assert loaded.approval_mode == "plan" and loaded.max_cost_usd == scheduler.DEFAULT_JOB_MAX_COST_USD

    def test_add_keeps_order_and_remove_deletes_only_the_named_job(self, env: Path) -> None:
        store = JobStore()
        for name in ("a", "b", "c"):
            store.add(_job(env, name))
        store.remove("b")
        assert [job.name for job in store.load()] == ["a", "c"]

    def test_a_duplicate_name_is_refused(self, env: Path) -> None:
        store = JobStore()
        store.add(_job(env))
        with pytest.raises(ScheduleError, match="already exists"):
            store.add(_job(env))

    def test_removing_or_getting_an_unknown_job_is_an_error(self, env: Path) -> None:
        with pytest.raises(ScheduleError, match="no job named 'x'"):
            JobStore().remove("x")
        with pytest.raises(ScheduleError, match="no job named 'x'"):
            JobStore().get("x")

    def test_automatic_names_skip_the_taken_ones(self, env: Path) -> None:
        store = JobStore()
        assert store.next_name() == "job-1"
        store.add(_job(env, "job-1"))
        store.add(_job(env, "job-3"))
        assert store.next_name() == "job-2"

    @pytest.mark.parametrize(("overrides", "message"), [
        ({"name": ""}, "job name"),
        ({"name": "../evil"}, "job name"),
        ({"name": "a/b"}, "job name"),
        ({"name": "-lead"}, "job name"),
        ({"name": "x" * 65}, "job name"),
        ({"schedule": "61 * * * *"}, "minute"),
        ({"schedule": "sometimes"}, "expected 5 cron fields"),
        ({"prompt": "   "}, "prompt is empty"),
        ({"cwd": ""}, "working directory"),
        ({"approval_mode": "yolo"}, "approval mode"),
        ({"max_cost_usd": -1.0}, "cannot be negative"),
    ])
    def test_invalid_jobs_are_refused(self, env: Path, overrides: dict[str, object], message: str) -> None:
        with pytest.raises(ScheduleError, match=message):
            validate_job(_job(env, **overrides))
        with pytest.raises(ScheduleError):
            JobStore().add(_job(env, **overrides))
        assert JobStore().load() == []

    @pytest.mark.parametrize(("text", "message"), [
        ("jobs: [", "jobs.yml"),
        ("- a\n- b\n", "'jobs' list"),
        ("jobs: 5\n", "'jobs' list"),
        ("jobs:\n  - just text\n", "expected a mapping"),
        ("jobs:\n  - {name: a, schedule: every 1m, prompt: p}\n", "missing 'cwd'"),
        ("jobs:\n  - {name: a, schedule: every 1m, prompt: p, cwd: /tmp, color: red}\n", "unknown key 'color'"),
        ("jobs:\n  - {name: a, schedule: every 1m, prompt: 7, cwd: /tmp}\n", "'prompt' has the wrong type"),
        ("jobs:\n  - {name: a, schedule: every 1m, prompt: p, cwd: /tmp, max_cost_usd: true}\n", "'max_cost_usd' has the wrong type"),
        ("jobs:\n  - {name: a, schedule: bad, prompt: p, cwd: /tmp}\n", "job 'a'"),
        ("jobs:\n  - {name: a, schedule: every 1m, prompt: p, cwd: /tmp}\n  - {name: a, schedule: every 2m, prompt: p, cwd: /tmp}\n", "appears twice"),
    ])
    def test_a_malformed_file_is_reported_with_the_path(self, env: Path, text: str, message: str) -> None:
        path = core_paths.schedule_jobs_path()
        path.parent.mkdir(parents=True)
        path.write_text(text, encoding="utf-8")
        with pytest.raises(ScheduleError) as caught:
            JobStore().load()
        assert str(path) in str(caught.value) and message in str(caught.value)

    def test_an_empty_file_is_an_empty_list(self, env: Path) -> None:
        path = core_paths.schedule_jobs_path()
        path.parent.mkdir(parents=True)
        path.write_text("", encoding="utf-8")
        assert JobStore().load() == []

    def test_an_explicit_path_is_used(self, tmp_path: Path, env: Path) -> None:
        store = JobStore(tmp_path / "other.yml")
        store.add(_job(env))
        assert (tmp_path / "other.yml").is_file() and not core_paths.schedule_jobs_path().exists()


class TestRunCommand:
    def test_the_default_command_is_this_interpreter_module(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(COMMAND_ENV, raising=False)
        assert scheduler_command() == [sys.executable, "-m", "nerdvana_cli.main"]

    def test_the_override_is_split_like_a_shell(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(COMMAND_ENV, "uv run 'my nerdvana'")
        assert scheduler_command() == ["uv", "run", "my nerdvana"]

    def test_the_command_carries_the_limits_and_the_prompt_last(self, env: Path) -> None:
        job = _job(env, max_cost_usd=0.75, approval_mode="plan", prompt="--looks like an option")
        command = build_run_command(job)
        tail = command[len(scheduler_command()):]
        assert tail == ["run", "--cwd", str(env), "--output-format", "json", "--approval-mode", "plan",
                        "--max-cost-usd", "0.75", "--require-price", "--", "--looks like an option"]

    def test_a_zero_ceiling_sends_no_cost_flags(self, env: Path) -> None:
        command = build_run_command(_job(env, max_cost_usd=0.0))
        assert "--max-cost-usd" not in command and "--require-price" not in command

    def test_every_flag_it_uses_exists_on_the_run_command(self, env: Path) -> None:
        import typer.main

        from nerdvana_cli.cli.runtime import APPROVAL_MODE_MAP
        from nerdvana_cli.main import app

        run    = typer.main.get_command(app).commands["run"]  # type: ignore[attr-defined]
        names  = {option for param in run.params for option in param.opts}
        flags  = {part for part in build_run_command(_job(env)) if part.startswith("--") and part != "--"}
        assert flags <= names
        assert set(scheduler.APPROVAL_MODES) <= set(APPROVAL_MODE_MAP)


class TestJobLock:
    def test_a_second_claim_fails_until_release(self, tmp_path: Path) -> None:
        first, second = JobLock(tmp_path / "a.lock"), JobLock(tmp_path / "a.lock")
        assert first.acquire() is True
        assert second.acquire() is False
        first.release()
        assert second.acquire() is True
        second.release()
        assert not (tmp_path / "a.lock").exists()

    def test_a_lock_left_by_a_dead_process_is_taken_over(self, tmp_path: Path) -> None:
        path = tmp_path / "a.lock"
        path.write_text("999999999", encoding="utf-8")
        lock = JobLock(path)
        assert lock.acquire() is True
        assert path.read_text(encoding="utf-8") == str(os.getpid())

    def test_a_lock_held_by_a_live_process_is_respected(self, tmp_path: Path) -> None:
        path = tmp_path / "a.lock"
        path.write_text(str(os.getpid()), encoding="utf-8")
        assert JobLock(path).acquire() is False
        assert path.exists()

    def test_a_half_written_lock_counts_while_it_is_fresh_and_not_when_old(self, tmp_path: Path) -> None:
        path = tmp_path / "a.lock"
        path.write_text("", encoding="utf-8")
        assert JobLock(path).acquire() is False
        old = time.time() - 60
        os.utime(path, (old, old))
        assert JobLock(path).acquire() is True

    def test_release_without_holding_leaves_the_file(self, tmp_path: Path) -> None:
        path = tmp_path / "a.lock"
        path.write_text(str(os.getpid()), encoding="utf-8")
        JobLock(path).release()
        assert path.exists()

    def test_hand_to_names_the_new_holder(self, tmp_path: Path) -> None:
        lock = JobLock(tmp_path / "a.lock")
        lock.acquire()
        lock.hand_to(4242)
        assert (tmp_path / "a.lock").read_text(encoding="utf-8") == "4242"
        lock.release()


class TestRunning:
    def test_a_run_saves_the_result_the_log_and_the_cost(self, env: Path, tmp_path: Path) -> None:
        record = run_job_now(_job(env), poll_seconds=0.02)
        assert record is not None
        assert (record.status, record.exit_code, record.cost_usd) == ("success", 0, 0.25)
        assert record.result["result"] == "all done"
        saved = json.loads(Path(record.record_path).read_text(encoding="utf-8"))
        assert saved["job"] == "nightly" and saved["result"]["total_cost_usd"] == 0.25
        assert Path(record.record_path).parent == core_paths.schedule_runs_dir() / "nightly"
        assert "fake stderr line" in Path(record.log_path).read_text(encoding="utf-8")
        assert not list(Path(record.record_path).parent.glob("*.out"))

    def test_the_process_gets_the_job_directory_and_arguments(self, env: Path, tmp_path: Path) -> None:
        run_job_now(_job(env, max_cost_usd=0.5, approval_mode="plan"), poll_seconds=0.02)
        (call,) = _calls(tmp_path)
        assert Path(str(call["cwd"])).resolve() == env.resolve()
        args = call["args"]
        assert isinstance(args, list)
        assert args[:1] == ["run"] and args[-2:] == ["--", "summarize the repo"]
        assert args[args.index("--approval-mode") + 1] == "plan"
        assert args[args.index("--max-cost-usd") + 1] == "0.5"

    @pytest.mark.parametrize(("exit_code", "status"), [(1, "failed"), (2, "config_error"), (3, "limit_reached"), (9, "failed")])
    def test_the_exit_code_decides_the_status(self, env: Path, monkeypatch: pytest.MonkeyPatch, exit_code: int, status: str) -> None:
        monkeypatch.setenv("FAKE_EXIT", str(exit_code))
        record = run_job_now(_job(env), poll_seconds=0.02)
        assert record is not None and (record.status, record.exit_code) == (status, exit_code)

    def test_output_that_is_not_json_is_kept_as_text(self, env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FAKE_RAW", "plain words")
        record = run_job_now(_job(env), poll_seconds=0.02)
        assert record is not None and record.result == {"stdout": "plain words\n"} and record.cost_usd == 0.0

    def test_a_missing_working_directory_is_a_launch_error_and_frees_the_lock(self, env: Path) -> None:
        with pytest.raises(LaunchError):
            start_job(_job(env, cwd=str(env / "missing")), T0)
        assert not core_paths.schedule_lock_path("nightly").exists()

    def test_an_unknown_command_is_a_launch_error(self, env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(COMMAND_ENV, "/no/such/binary")
        with pytest.raises(LaunchError, match="/no/such/binary"):
            start_job(_job(env), T0)

    def test_two_runs_of_one_job_never_overlap(self, env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FAKE_SLEEP", "30")
        first = start_job(_job(env), T0)
        assert first is not None
        try:
            assert start_job(_job(env), T0) is None
            assert run_job_now(_job(env), poll_seconds=0.02) is None
        finally:
            scheduler._terminate(first)
            finish_job(first, "interrupted")
        assert not core_paths.schedule_lock_path("nightly").exists()

    def test_the_lock_names_the_run_process_not_the_scheduler(self, env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FAKE_SLEEP", "30")
        running = start_job(_job(env), T0)
        assert running is not None
        try:
            assert core_paths.schedule_lock_path("nightly").read_text(encoding="utf-8") == str(running.process.pid)
        finally:
            scheduler._terminate(running)
            finish_job(running, "interrupted")

    def test_a_run_that_passes_the_time_limit_is_stopped_and_recorded(self, env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FAKE_SLEEP", "30")
        monkeypatch.setattr(scheduler, "RUN_TIMEOUT_SECONDS", 0.3)
        running = start_job(_job(env), T0)
        assert running is not None
        record = None
        deadline = time.monotonic() + 20
        while record is None and time.monotonic() < deadline:
            record = poll_job(running)
            time.sleep(0.05)
        assert record is not None and record.status == "timeout"
        assert running.process.poll() is not None
        assert not core_paths.schedule_lock_path("nightly").exists()

    def test_skipped_runs_are_recorded_without_a_process(self, env: Path) -> None:
        record = record_skipped(_job(env), T0, "skipped_overlap", "busy")
        saved = json.loads(Path(record.record_path).read_text(encoding="utf-8"))
        assert saved["status"] == "skipped_overlap" and saved["exit_code"] is None and saved["note"] == "busy"

    def test_the_latest_record_is_the_newest_one(self, env: Path) -> None:
        assert latest_record("nightly") is None
        record_skipped(_job(env), T0, "skipped_overlap", "first")
        record_skipped(_job(env), T0 + timedelta(minutes=1), "skipped_budget", "second")
        latest = latest_record("nightly")
        assert latest is not None and latest["note"] == "second"

    def test_the_daily_cost_adds_up_the_records_of_that_day_only(self, env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FAKE_COST", "0.4")
        run_job_now(_job(env), poll_seconds=0.02)
        run_job_now(_job(env, name="other"), poll_seconds=0.02)
        today = datetime.now().date()
        assert spent_on(today) == pytest.approx(0.8)
        assert spent_on(today - timedelta(days=1)) == 0.0

    def test_an_unreadable_record_does_not_break_the_daily_cost(self, env: Path) -> None:
        directory = core_paths.schedule_runs_dir() / "x"
        directory.mkdir(parents=True)
        (directory / "20260105T120000000000.json").write_text("{not json", encoding="utf-8")
        (directory / "20260105T120100000000.json").write_text(json.dumps({"cost_usd": 0.5}), encoding="utf-8")
        assert spent_on(date(2026, 1, 5)) == 0.5


class TestScheduler:
    def _scheduler(self, env: Path, *jobs: Job, cap: float = 100.0) -> Scheduler:
        store = JobStore()
        for job in jobs:
            store.add(job)
        return Scheduler(store, cap)

    def test_a_job_is_not_started_on_the_first_tick_even_when_a_firing_was_missed(self, env: Path, tmp_path: Path) -> None:
        scheduler_ = self._scheduler(env, _job(env, schedule="0 3 * * *"))
        assert scheduler_.tick(datetime(2026, 1, 5, 12, 0, 30)) == []
        assert scheduler_.running == {} and _calls(tmp_path) == []

    def test_a_cron_job_starts_once_in_its_minute(self, env: Path, tmp_path: Path) -> None:
        scheduler_ = self._scheduler(env, _job(env, schedule="1 12 * * *"))
        scheduler_.tick(T0)
        assert scheduler_.tick(datetime(2026, 1, 5, 12, 0, 50)) == []
        assert scheduler_.tick(datetime(2026, 1, 5, 12, 1, 5)) == ["started nightly"]
        _drain(scheduler_)
        assert scheduler_.tick(datetime(2026, 1, 5, 12, 1, 20)) == []
        assert scheduler_.tick(datetime(2026, 1, 5, 12, 1, 40)) == []
        assert len(_calls(tmp_path)) == 1

    def test_a_long_sleep_does_not_replay_the_missed_firings(self, env: Path, tmp_path: Path) -> None:
        scheduler_ = self._scheduler(env, _job(env, schedule="0 3 * * *"))
        scheduler_.tick(datetime(2026, 1, 5, 2, 0, 0))
        assert scheduler_.tick(datetime(2026, 1, 5, 5, 0, 0)) == []
        assert _calls(tmp_path) == []

    def test_a_short_gap_around_the_firing_still_fires(self, env: Path) -> None:
        scheduler_ = self._scheduler(env, _job(env, schedule="0 3 * * *"))
        scheduler_.tick(datetime(2026, 1, 5, 2, 58, 0))
        assert scheduler_.tick(datetime(2026, 1, 5, 3, 0, 20)) == ["started nightly"]
        _drain(scheduler_)

    def test_an_interval_job_counts_from_the_daemon_start_and_after_each_run(self, env: Path, tmp_path: Path) -> None:
        scheduler_ = self._scheduler(env, _job(env, schedule="every 10m"))
        scheduler_.tick(T0)
        assert scheduler_.tick(T0 + timedelta(minutes=9, seconds=59)) == []
        assert scheduler_.tick(T0 + timedelta(minutes=10)) == ["started nightly"]
        _drain(scheduler_)
        assert scheduler_.tick(T0 + timedelta(minutes=19)) == []
        assert scheduler_.tick(T0 + timedelta(minutes=20)) == ["started nightly"]
        _drain(scheduler_)
        assert len(_calls(tmp_path)) == 2

    def test_finished_runs_are_reported_with_status_and_cost(self, env: Path) -> None:
        scheduler_ = self._scheduler(env, _job(env))
        scheduler_.tick(T0)
        scheduler_.tick(T0 + timedelta(minutes=1))
        lines = _drain(scheduler_)
        assert len(lines) == 1 and lines[0].startswith("finished nightly: success, cost $0.2500")

    def test_a_job_still_running_when_due_again_is_skipped_and_recorded(self, env: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv("FAKE_SLEEP", "30")
        scheduler_ = self._scheduler(env, _job(env))
        scheduler_.tick(T0)
        assert scheduler_.tick(T0 + timedelta(minutes=1)) == ["started nightly"]
        try:
            assert scheduler_.tick(T0 + timedelta(minutes=2)) == ["skipped nightly: the previous run is still going"]
            skipped = [json.loads(p.read_text(encoding="utf-8")) for p in (core_paths.schedule_runs_dir() / "nightly").glob("*.json")]
            assert [item["status"] for item in skipped] == ["skipped_overlap"]
        finally:
            scheduler_.shutdown()

    def test_a_run_started_elsewhere_blocks_the_daemon(self, env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FAKE_SLEEP", "30")
        elsewhere = start_job(_job(env), T0)
        assert elsewhere is not None
        scheduler_ = self._scheduler(env, _job(env))
        try:
            scheduler_.tick(T0)
            assert scheduler_.tick(T0 + timedelta(minutes=1)) == ["skipped nightly: the previous run is still going"]
        finally:
            scheduler._terminate(elsewhere)
            finish_job(elsewhere, "interrupted")

    def test_the_daily_ceiling_stops_new_runs(self, env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FAKE_COST", "0.6")
        scheduler_ = self._scheduler(env, _job(env), cap=1.0)
        scheduler_.tick(T0)
        results = []
        for minute in (1, 2, 3):
            results.extend(scheduler_.tick(T0 + timedelta(minutes=minute)))
            _drain(scheduler_)
        assert results == ["started nightly", "started nightly", "skipped nightly: the daily cost ceiling of $1 is reached"]
        statuses = sorted(json.loads(p.read_text(encoding="utf-8"))["status"] for p in (core_paths.schedule_runs_dir() / "nightly").glob("*.json"))
        assert statuses == ["skipped_budget", "success", "success"]

    def test_runs_in_flight_count_against_the_ceiling_with_their_own_limits(self, env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FAKE_SLEEP", "30")
        jobs = [_job(env, name=name, schedule="every 1m", max_cost_usd=0.6) for name in ("a", "b", "c")]
        scheduler_ = self._scheduler(env, *jobs, cap=1.0)
        scheduler_.tick(T0)
        try:
            lines = scheduler_.tick(T0 + timedelta(minutes=1))
            assert lines == ["started a", "started b", "skipped c: the daily cost ceiling of $1 is reached"]
        finally:
            scheduler_.shutdown()

    def test_a_ceiling_of_zero_means_no_ceiling(self, env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FAKE_COST", "50")
        scheduler_ = self._scheduler(env, _job(env), cap=0.0)
        scheduler_.tick(T0)
        for minute in (1, 2, 3):
            assert scheduler_.tick(T0 + timedelta(minutes=minute)) == ["started nightly"]
            _drain(scheduler_)

    def test_jobs_added_while_running_start_counting_when_first_seen(self, env: Path, tmp_path: Path) -> None:
        scheduler_ = self._scheduler(env)
        scheduler_.tick(T0)
        JobStore().add(_job(env, schedule="every 5m"))
        assert scheduler_.tick(T0 + timedelta(minutes=30)) == []
        assert scheduler_.tick(T0 + timedelta(minutes=35)) == ["started nightly"]
        _drain(scheduler_)

    def test_a_removed_job_is_no_longer_started(self, env: Path) -> None:
        scheduler_ = self._scheduler(env, _job(env))
        scheduler_.tick(T0)
        JobStore().remove("nightly")
        assert scheduler_.tick(T0 + timedelta(minutes=5)) == []

    def test_an_unreadable_job_file_keeps_the_last_list(self, env: Path) -> None:
        scheduler_ = self._scheduler(env, _job(env))
        scheduler_.tick(T0)
        core_paths.schedule_jobs_path().write_text("jobs: [", encoding="utf-8")
        lines = scheduler_.tick(T0 + timedelta(minutes=1))
        assert lines[0].startswith("cannot read the job file, keeping the last list") and "started nightly" in lines
        _drain(scheduler_)

    def test_a_job_that_cannot_start_is_recorded_as_an_error(self, env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(COMMAND_ENV, "/no/such/binary")
        scheduler_ = self._scheduler(env, _job(env))
        scheduler_.tick(T0)
        (line,) = scheduler_.tick(T0 + timedelta(minutes=1))
        assert line.startswith("failed to start nightly")
        assert latest_record("nightly")["status"] == "error"  # type: ignore[index]

    def test_shutdown_stops_running_jobs_and_records_them(self, env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FAKE_SLEEP", "30")
        scheduler_ = self._scheduler(env, _job(env))
        scheduler_.tick(T0)
        scheduler_.tick(T0 + timedelta(minutes=1))
        process = scheduler_.running["nightly"].process
        assert scheduler_.shutdown() == ["stopped nightly: interrupted"]
        assert process.poll() is not None and scheduler_.running == {}
        assert latest_record("nightly")["status"] == "interrupted"  # type: ignore[index]
        assert not core_paths.schedule_lock_path("nightly").exists()


class TestServe:
    def test_it_ticks_with_the_clock_until_told_to_stop(self, env: Path) -> None:
        store = JobStore()
        store.add(_job(env, schedule="every 1m"))
        clock_values = iter(T0 + timedelta(seconds=30 * step) for step in range(100))
        slept: list[float] = []
        emitted: list[str] = []
        scheduler_ = Scheduler(store, 100.0)

        def stop() -> bool:
            return len(slept) >= 5

        def sleep(seconds: float) -> None:
            slept.append(seconds)
            _drain(scheduler_)

        serve(scheduler_, 30.0, emitted.append, clock=lambda: next(clock_values), sleep=sleep, should_stop=stop)
        assert slept == [30.0] * 5
        assert any("started nightly" in line for line in emitted)
        assert all(line[:4] == "2026" for line in emitted)
        assert scheduler_.running == {}

    def test_running_jobs_are_stopped_when_the_loop_is_interrupted(self, env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FAKE_SLEEP", "30")
        store = JobStore()
        store.add(_job(env, schedule="every 1m"))
        scheduler_ = Scheduler(store, 100.0)
        times = iter([T0, T0 + timedelta(minutes=1), T0 + timedelta(minutes=1), T0 + timedelta(minutes=1), T0 + timedelta(minutes=1)])
        calls = {"n": 0}

        def sleeper(_seconds: float) -> None:
            calls["n"] += 1
            if calls["n"] == 2:
                raise KeyboardInterrupt

        emitted: list[str] = []
        with pytest.raises(KeyboardInterrupt):
            serve(scheduler_, 1.0, emitted.append, clock=lambda: next(times), sleep=sleeper)
        assert scheduler_.running == {}
        assert any("stopped nightly: interrupted" in line for line in emitted)


class TestSystemdUnit:
    def test_the_unit_runs_the_daemon_and_is_only_printed(self, env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(COMMAND_ENV, raising=False)
        unit = systemd_unit()
        assert f"ExecStart={sys.executable} -m nerdvana_cli.main schedule daemon\n" in unit
        assert "[Service]" in unit and "[Install]" in unit and "WantedBy=default.target" in unit
        assert "systemctl --user enable --now nerdvana-schedule.service" in unit

    def test_the_command_override_is_used(self, env: Path) -> None:
        assert f"ExecStart={sys.executable} {env.parent / 'fake_nerdvana.py'} schedule daemon\n" in systemd_unit()
