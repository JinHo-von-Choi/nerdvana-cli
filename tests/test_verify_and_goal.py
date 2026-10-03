"""The verification runner and the goal state it feeds.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from nerdvana_cli.core.goal import ACTIVE, MET, PAUSED, UNMET, Goal, GoalError, load_goal, save_goal
from nerdvana_cli.core.safety import sandbox
from nerdvana_cli.core.safety.sandbox import SandboxPolicy
from nerdvana_cli.core.verify import run_verify

# ---------------------------------------------------------------------------
# Running the command
# ---------------------------------------------------------------------------


async def test_exit_zero_passes_and_other_codes_fail_with_the_output(tmp_path: Path) -> None:
    ok = await run_verify("echo fine", str(tmp_path))
    assert (ok.passed, ok.exit_code) == (True, 0)
    bad = await run_verify("echo 'assert failed: x' ; exit 3", str(tmp_path))
    assert (bad.passed, bad.exit_code) == (False, 3)
    assert "assert failed: x" in bad.tail


async def test_standard_error_is_part_of_the_output(tmp_path: Path) -> None:
    result = await run_verify("echo oops >&2; exit 1", str(tmp_path))
    assert "oops" in result.tail


async def test_the_command_runs_in_the_given_directory(tmp_path: Path) -> None:
    (tmp_path / "marker.txt").write_text("here")
    assert (await run_verify("test -f marker.txt", str(tmp_path))).passed


async def test_a_long_output_is_cut_to_its_end(tmp_path: Path) -> None:
    result = await run_verify("seq 1 20000", str(tmp_path), tail=200)
    assert len(result.tail) < 400
    assert result.tail.startswith("[output cut]")
    assert result.tail.rstrip().endswith("20000")


async def test_a_command_over_its_time_is_stopped_with_everything_it_started(tmp_path: Path) -> None:
    marker  = tmp_path / "child-alive"
    command = f"(sleep 2; touch {marker}) & sleep 30"
    started = time.monotonic()
    result  = await run_verify(command, str(tmp_path), timeout=0.5)
    assert result.timed_out and not result.passed
    assert time.monotonic() - started < 5
    time.sleep(2.5)
    assert not marker.exists()  # the background child died with the group


async def test_a_missing_command_is_a_failure_not_an_exception(tmp_path: Path) -> None:
    result = await run_verify("definitely-not-a-command-xyz", str(tmp_path))
    assert not result.passed and result.exit_code != 0


async def test_an_unusable_sandbox_requirement_runs_nothing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(sandbox, "landlock_abi", lambda: 0)
    result = await run_verify(f"touch {tmp_path}/ran", str(tmp_path), policy=SandboxPolicy("require"))
    assert not result.passed and "sandbox required" in result.note
    assert not (tmp_path / "ran").exists()


@pytest.mark.skipif(sandbox.landlock_abi() < 1, reason="the kernel has no Landlock")
async def test_the_sandbox_policy_confines_the_verification_command(tmp_path: Path) -> None:
    outside = Path(__file__).parent / f".verify-outside-{tmp_path.name}"
    outside.mkdir()
    try:
        project = tmp_path / "project"
        project.mkdir()
        result = await run_verify(f"echo x > {outside}/leak.txt", str(project), policy=SandboxPolicy("require"))
        assert not result.passed
        assert not (outside / "leak.txt").exists()
    finally:
        for leftover in outside.iterdir():
            leftover.unlink()
        outside.rmdir()


def test_the_summary_names_the_outcome() -> None:
    from nerdvana_cli.core.verify import VerifyResult

    assert VerifyResult(True, 0, False, 1.25, "").summary() == "exit 0 in 1.2s"
    assert "timed out" in VerifyResult(False, -1, True, 30.0, "").summary()


# ---------------------------------------------------------------------------
# Goal state
# ---------------------------------------------------------------------------


@pytest.fixture()
def home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setenv("NERDVANA_DATA_HOME", str(tmp_path / "data"))
    return tmp_path / "data"


def test_a_goal_needs_a_command_and_a_known_status() -> None:
    with pytest.raises(GoalError):
        Goal(objective="x", verify="   ")
    with pytest.raises(GoalError):
        Goal(objective="x", verify="true", status="mystery")
    with pytest.raises(GoalError):
        Goal(objective="x", verify="true", max_attempts=0)


def test_a_passing_run_meets_the_goal_and_failures_use_up_the_attempts() -> None:
    goal = Goal(objective="fix it", verify="pytest", max_attempts=2)
    goal.record_attempt(False, 1, "boom")
    assert (goal.status, goal.attempts, goal.last_exit, goal.last_tail) == (ACTIVE, 1, 1, "boom")
    goal.record_attempt(False, 1, "boom again")
    assert goal.status == UNMET
    met = Goal(objective="x", verify="true")
    met.record_attempt(True, 0, "")
    assert met.status == MET and not met.enforced


def test_only_an_active_goal_is_enforced() -> None:
    assert Goal(objective="x", verify="true").enforced
    assert not Goal(objective="x", verify="true", status=PAUSED).enforced


def test_a_goal_survives_a_round_trip_through_the_session_file(home: Path) -> None:
    goal = Goal(objective="fix it", verify="pytest -q", attempts=2, last_exit=1, last_tail="tail")
    save_goal("session-1", goal)
    assert load_goal("session-1") == goal
    assert load_goal("another") is None
    save_goal("session-1", None)
    assert load_goal("session-1") is None


def test_session_ids_cannot_escape_the_goals_directory(home: Path) -> None:
    save_goal("../../etc/evil", Goal(objective="x", verify="true"))
    assert not (home.parent / "etc").exists()
    assert [p.name for p in (home / "goals").iterdir()] == [".._.._etc_evil.json"]


def test_a_damaged_or_unknown_file_is_ignored(home: Path) -> None:
    (home / "goals").mkdir(parents=True)
    (home / "goals" / "s.json").write_text("{not json", encoding="utf-8")
    assert load_goal("s") is None
    (home / "goals" / "t.json").write_text('{"objective": "x", "verify": "true", "future_field": 1}', encoding="utf-8")
    goal = load_goal("t")
    assert goal is not None and (goal.objective, goal.verify, goal.status) == ("x", "true", ACTIVE)


def test_saving_leaves_no_temporary_files(home: Path) -> None:
    save_goal("s", Goal(objective="x", verify="true"))
    assert [p.name for p in (home / "goals").iterdir()] == ["s.json"]
    assert os.access(home / "goals" / "s.json", os.R_OK)
