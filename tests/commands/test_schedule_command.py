"""`nerdvana schedule ...` through the real Typer app, with a fake nerdvana command for `run`.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from nerdvana_cli.cli.scheduler import COMMAND_ENV, JobStore
from nerdvana_cli.commands import schedule_command
from nerdvana_cli.core import paths as core_paths
from nerdvana_cli.main import app

FAKE = (
    "import json, os, sys\n"
    "print(json.dumps({'type': 'result', 'result': 'fake answer', 'total_cost_usd': 0.125}))\n"
    "sys.exit(int(os.environ.get('FAKE_EXIT', '0')))\n"
)


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    script = tmp_path / "fake_nerdvana.py"
    script.write_text(FAKE, encoding="utf-8")
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.setenv("NERDVANA_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv(COMMAND_ENV, f"{sys.executable} {script}")
    monkeypatch.delenv("FAKE_EXIT", raising=False)
    monkeypatch.chdir(work)
    return work


def _invoke(*args: str):  # type: ignore[no-untyped-def]
    return CliRunner().invoke(app, ["--no-update-check", "schedule", *args])


class TestAdd:
    def test_it_stores_the_job_and_names_the_next_run(self, env: Path) -> None:
        result = _invoke("add", "0 3 * * *", "--prompt", "check the build", "--name", "nightly", "--max-cost-usd", "0.5")
        assert result.exit_code == 0, result.output
        assert "Added job nightly" in result.output and "03:00" in result.output
        (job,) = JobStore().load()
        assert (job.name, job.schedule, job.prompt, job.max_cost_usd, job.approval_mode) == ("nightly", "0 3 * * *", "check the build", 0.5, "plan")
        assert job.cwd == str(env.resolve()) and job.created_at

    def test_an_interval_and_an_explicit_directory_and_mode(self, env: Path, tmp_path: Path) -> None:
        other = tmp_path / "other"
        other.mkdir()
        result = _invoke("add", "every 15m", "--prompt", "p", "--cwd", str(other), "--approval-mode", "default")
        assert result.exit_code == 0, result.output
        (job,) = JobStore().load()
        assert job.name == "job-1" and job.cwd == str(other.resolve()) and job.approval_mode == "default"

    def test_the_default_mode_is_read_only(self, env: Path) -> None:
        _invoke("add", "every 1h", "--prompt", "p")
        assert JobStore().load()[0].approval_mode == "plan"

    @pytest.mark.parametrize("args", [
        ["add", "not a schedule", "--prompt", "p"],
        ["add", "61 * * * *", "--prompt", "p"],
        ["add", "0 0 30 2 *", "--prompt", "p"],
        ["add", "every 1m", "--prompt", "  "],
        ["add", "every 1m", "--prompt", "p", "--approval-mode", "yolo"],
        ["add", "every 1m", "--prompt", "p", "--max-cost-usd", "-1"],
        ["add", "every 1m", "--prompt", "p", "--name", "../x"],
        ["add", "every 1m", "--prompt", "p", "--cwd", "/no/such/dir"],
    ])
    def test_bad_input_is_refused_and_nothing_is_stored(self, env: Path, args: list[str]) -> None:
        result = _invoke(*args)
        assert result.exit_code == 2
        assert JobStore().load() == []

    def test_a_duplicate_name_is_refused(self, env: Path) -> None:
        _invoke("add", "every 1m", "--prompt", "p", "--name", "x")
        result = _invoke("add", "every 2m", "--prompt", "q", "--name", "x")
        assert result.exit_code == 2 and "already exists" in result.output
        assert [job.prompt for job in JobStore().load()] == ["p"]

    def test_the_prompt_is_required(self, env: Path) -> None:
        assert _invoke("add", "every 1m").exit_code == 2


class TestListAndRemove:
    def test_an_empty_list(self, env: Path) -> None:
        result = _invoke("list")
        assert result.exit_code == 0 and "No scheduled jobs" in result.output

    def test_list_shows_the_jobs_and_the_last_outcome(self, env: Path) -> None:
        _invoke("add", "0 3 * * *", "--prompt", "p", "--name", "nightly")
        _invoke("add", "every 5m", "--prompt", "q", "--name", "poll")
        before = _invoke("list")
        assert "nightly" in before.output and "poll" in before.output and "never" in before.output
        assert _invoke("run", "poll").exit_code == 0
        after = _invoke("list")
        assert "success at" in after.output

    def test_remove_deletes_the_job_and_keeps_its_records(self, env: Path) -> None:
        _invoke("add", "every 5m", "--prompt", "p", "--name", "poll")
        _invoke("run", "poll")
        result = _invoke("remove", "poll")
        assert result.exit_code == 0 and JobStore().load() == []
        assert list((core_paths.schedule_runs_dir() / "poll").glob("*.json"))

    def test_removing_an_unknown_job_fails(self, env: Path) -> None:
        result = _invoke("remove", "ghost")
        assert result.exit_code == 2 and "no job named 'ghost'" in result.output

    def test_a_malformed_job_file_is_reported(self, env: Path) -> None:
        path = core_paths.schedule_jobs_path()
        path.parent.mkdir(parents=True)
        path.write_text("jobs: [", encoding="utf-8")
        result = _invoke("list")
        assert result.exit_code == 2 and "jobs.yml" in result.output.replace("\n", "")


class TestRun:
    def test_it_runs_once_prints_the_answer_and_saves_the_record(self, env: Path) -> None:
        _invoke("add", "every 5m", "--prompt", "p", "--name", "poll")
        result = _invoke("run", "poll")
        assert result.exit_code == 0, result.output
        assert "fake answer" in result.output and "success" in result.output and "$0.1250" in result.output
        (record,) = (core_paths.schedule_runs_dir() / "poll").glob("*.json")
        assert json.loads(record.read_text(encoding="utf-8"))["cost_usd"] == 0.125

    def test_the_exit_code_of_the_run_is_passed_on(self, env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _invoke("add", "every 5m", "--prompt", "p", "--name", "poll")
        monkeypatch.setenv("FAKE_EXIT", "3")
        result = _invoke("run", "poll")
        assert result.exit_code == 3 and "limit_reached" in result.output

    def test_an_unknown_job_fails(self, env: Path) -> None:
        assert _invoke("run", "ghost").exit_code == 2

    def test_a_run_already_going_is_refused(self, env: Path) -> None:
        lock = core_paths.schedule_lock_path("poll")
        lock.parent.mkdir(parents=True)
        import os

        lock.write_text(str(os.getpid()), encoding="utf-8")
        _invoke("add", "every 5m", "--prompt", "p", "--name", "poll")
        result = _invoke("run", "poll")
        assert result.exit_code == 1 and "already going" in result.output

    def test_a_command_that_cannot_start_fails_cleanly(self, env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _invoke("add", "every 5m", "--prompt", "p", "--name", "poll")
        monkeypatch.setenv(COMMAND_ENV, "/no/such/binary")
        result = _invoke("run", "poll")
        assert result.exit_code == 1 and "cannot start" in result.output.replace("\n", "")


class TestDaemon:
    def test_it_reports_its_settings_and_hands_the_loop_a_scheduler(self, env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _invoke("add", "every 5m", "--prompt", "p", "--name", "poll")
        seen: dict[str, object] = {}
        monkeypatch.setattr(schedule_command.signal, "signal", lambda *_args: None)
        monkeypatch.setattr(schedule_command, "serve", lambda scheduler, tick, emit: seen.update(cap=scheduler.daily_cap_usd, tick=tick))
        result = _invoke("daemon", "--max-daily-cost-usd", "2.5", "--tick-seconds", "5")
        assert result.exit_code == 0, result.output
        assert "1 job(s)" in result.output and "$2.5" in result.output
        assert seen == {"cap": 2.5, "tick": 5.0}

    def test_an_interrupt_ends_the_loop_quietly(self, env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        def interrupted(*_args: object, **_kwargs: object) -> None:
            raise KeyboardInterrupt

        monkeypatch.setattr(schedule_command.signal, "signal", lambda *_args: None)
        monkeypatch.setattr(schedule_command, "serve", interrupted)
        result = _invoke("daemon")
        assert result.exit_code == 0 and "scheduler stopped" in result.output

    def test_a_malformed_job_file_stops_the_start(self, env: Path) -> None:
        path = core_paths.schedule_jobs_path()
        path.parent.mkdir(parents=True)
        path.write_text("jobs: 5\n", encoding="utf-8")
        assert _invoke("daemon").exit_code == 2

    def test_the_tick_is_at_least_one_second(self, env: Path) -> None:
        assert _invoke("daemon", "--tick-seconds", "0").exit_code == 2


class TestInstallSystemd:
    def test_it_prints_a_unit_and_installs_nothing(self, env: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("HOME", str(tmp_path / "home"))
        result = _invoke("install-systemd")
        assert result.exit_code == 0
        assert "[Unit]" in result.output and "[Service]" in result.output and "schedule daemon" in result.output
        assert not (tmp_path / "home").exists()

    def test_the_unit_is_valid_ini_text(self, env: Path) -> None:
        import configparser

        parser = configparser.ConfigParser(interpolation=None, strict=False)
        parser.optionxform = str  # type: ignore[assignment,method-assign]
        parser.read_string(_invoke("install-systemd").output)
        assert parser["Service"]["ExecStart"].endswith("schedule daemon") and parser["Install"]["WantedBy"] == "default.target"
