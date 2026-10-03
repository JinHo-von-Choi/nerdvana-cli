"""scripts/bench_agent.py: pass^k, the environment record, repository isolation and the network option.

Author: 최진호
Date:   2026-10-03

No model is called and nothing touches the network.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest
import yaml

SCRIPT = Path(__file__).parent.parent / "scripts" / "bench_agent.py"
TASKS  = Path(__file__).parent.parent / "benchmarks" / "tasks"
FIXTURE = Path(__file__).parent.parent / "benchmarks" / "fixtures" / "sum-range"


def _load() -> ModuleType:
    spec   = importlib.util.spec_from_file_location("bench_agent", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["bench_agent"] = module
    spec.loader.exec_module(module)
    return module


bench = _load()


def _options(**changes: object) -> argparse.Namespace:
    base = {"approval_mode": "yolo", "sandbox": "require", "gate": False, "set": [], "model": "", "provider": "", "isolate": False, "no_network": False}
    return argparse.Namespace(**{**base, **changes})


def _sum_range() -> object:
    return next(task for task in bench.load_tasks(TASKS) if task.id == "sum-range")


def _git(cwd: Path, *args: str) -> str:
    done = subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=cwd, capture_output=True, text=True, check=False)
    return done.stdout.strip() if done.returncode == 0 else f"ERR:{done.stderr}"


# ---------------------------------------------------------------------------
# pass^k and the summary warnings
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("n", "c", "k", "expected"), [
    (4, 0, 2, 0.0),
    (4, 4, 4, 1.0),
    (4, 3, 2, 0.5),
    (4, 2, 2, 1 / 6),
    (4, 3, 4, 0.0),
    (3, 3, 10, 1.0),
    (3, 2, 10, 0.0),
    (1, 1, 1, 1.0),
    (0, 0, 1, 0.0),
    (4, 2, 0, 0.0),
])
def test_pass_hat_k_follows_the_unbiased_estimator_and_its_edges(n: int, c: int, k: int, expected: float) -> None:
    assert bench.pass_hat_k(n, c, k) == pytest.approx(expected)


def test_the_summary_carries_pass_hat_k_per_task_and_overall() -> None:
    attempts = [bench.Attempt("a", i, i < 4) for i in range(1, 5)] + [bench.Attempt("b", i, True) for i in range(1, 5)]
    summary  = bench.summarize(attempts, 2)
    by_task  = {t["task"]: t for t in summary["tasks"]}
    assert by_task["a"]["pass_hat_2"] == pytest.approx(0.5)
    assert by_task["b"]["pass_hat_2"] == pytest.approx(1.0)
    assert summary["mean_pass_hat_k"] == pytest.approx(0.75)
    assert summary["mean_pass_at_k"] == pytest.approx(1.0)
    assert "pass^2" in bench.render(summary)
    assert json.dumps(summary)


def test_the_summary_warns_when_a_task_has_fewer_than_three_attempts() -> None:
    thin = bench.summarize([bench.Attempt("a", 1, True), bench.Attempt("a", 2, True), bench.Attempt("b", 1, True)], 2)
    full = bench.summarize([bench.Attempt("a", i, True) for i in range(1, 4)], 3)
    assert thin["warnings"] and "4 or more" in thin["warnings"][0]
    assert "WARNING" in bench.render(thin)
    assert full["warnings"] == []


def test_the_dry_run_recommends_four_attempts_until_it_is_given_them(capsys: pytest.CaptureFixture[str]) -> None:
    assert bench.main([str(TASKS), "--attempts", "1"]) == 0
    assert "--attempts 4 or more" in capsys.readouterr().out
    assert bench.main([str(TASKS), "--attempts", "4"]) == 0
    assert "recommended" not in capsys.readouterr().out


def test_the_help_text_recommends_four_attempts(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        bench.parse_args(["--help"])
    assert "4 or more" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# The environment record
# ---------------------------------------------------------------------------


def test_the_environment_record_carries_the_facts_that_change_scores() -> None:
    env = bench.run_environment(_options(model="m1", provider="p1", no_network=True, isolate=True, set=["a.b=1", "c.d=2"]))
    for key in ("cpu_count", "memory_total_mb", "nerdvana_version", "git_commit", "git_dirty", "model", "provider", "sandbox", "network", "isolate", "overrides"):
        assert key in env
    assert (env["model"], env["provider"], env["sandbox"], env["network"], env["isolate"]) == ("m1", "p1", "require", False, True)
    assert env["overrides"] == "a.b=1, c.d=2"
    assert env["cpu_count"] and env["nerdvana_version"]


def test_an_attempt_records_its_limits_and_the_model_that_answered(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    script = "import json\nprint(json.dumps({'type': 'result', 'subtype': 'success', 'model': 'escalated', 'provider': 'prov'}))\n"
    monkeypatch.setattr(bench, "agent_command", lambda t, o: [sys.executable, "-c", script])
    attempt = bench.run_attempt(_sum_range(), 1, _options(), tmp_path, run_env={"model": "asked", "cpu_count": 8})
    env     = attempt.environment
    assert (env["model"], env["provider"], env["cpu_count"]) == ("escalated", "prov", 8)
    assert (env["timeout_s"], env["max_turns"], env["max_cost_usd"]) == (600, 15, 0.5)
    assert json.dumps(bench.asdict(attempt))


def test_the_summary_environment_keeps_agreed_values_and_lists_the_others() -> None:
    merged = bench.merge_environments([{"cpu": 8, "timeout_s": 60}, {"cpu": 8, "timeout_s": 600}, {"cpu": 8, "timeout_s": 60}])
    assert merged == {"cpu": 8, "timeout_s": [60, 600]}
    assert bench.merge_environments([]) == {}
    summary = bench.summarize([bench.Attempt("a", 1, True, environment={"cpu": 8}), bench.Attempt("a", 2, True, environment={"cpu": 8})], 2)
    assert summary["environment"] == {"cpu": 8}
    assert "environment: cpu=8" in bench.render(summary)


# ---------------------------------------------------------------------------
# Repository isolation
# ---------------------------------------------------------------------------


def test_isolation_leaves_one_commit_and_no_trace_of_the_older_ones(tmp_path: Path) -> None:
    _git(tmp_path, "init", "-q")
    (tmp_path / "f.txt").write_text("v1\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "first")
    (tmp_path / "f.txt").write_text("v2\n")
    _git(tmp_path, "commit", "-q", "-am", "second")
    older = _git(tmp_path, "rev-parse", "HEAD~1")
    assert bench.isolate_repository(tmp_path) == ""
    assert _git(tmp_path, "rev-list", "--count", "--all") == "1"
    assert _git(tmp_path, "cat-file", "-e", older).startswith("ERR:")
    assert (tmp_path / "f.txt").read_text() == "v2\n"
    assert _git(tmp_path, "status", "--porcelain") == ""


def test_isolation_makes_a_repository_of_a_plain_copy(tmp_path: Path) -> None:
    (tmp_path / "f.txt").write_text("x\n")
    assert bench.isolate_repository(tmp_path) == ""
    assert _git(tmp_path, "rev-list", "--count", "HEAD") == "1"


def test_prepare_isolates_before_the_setup_command_runs(tmp_path: Path) -> None:
    task = bench.Task("t", "p", "v", path=str(FIXTURE), setup="git rev-list --count HEAD > count.txt")
    assert bench.prepare(task, tmp_path / "w", isolate=True) == ""
    assert (tmp_path / "w" / "count.txt").read_text().strip() == "1"


def test_without_isolate_a_copied_directory_is_not_made_a_repository(tmp_path: Path) -> None:
    task = bench.Task("t", "p", "v", path=str(FIXTURE))
    assert bench.prepare(task, tmp_path / "w") == ""
    assert not (tmp_path / "w" / ".git").exists()


# ---------------------------------------------------------------------------
# The network option
# ---------------------------------------------------------------------------


def _user_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, text: str) -> None:
    source = tmp_path / "user.yml"
    source.write_text(text, encoding="utf-8")
    monkeypatch.setenv("NERDVANA_CONFIG", str(source))


def test_the_no_network_config_cuts_the_network_and_keeps_what_the_user_configured(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _user_config(tmp_path, monkeypatch, "model:\n  model: m9\npermissions:\n  always_deny: [Bash(rm *)]\nsandbox:\n  write_paths: [/data]\n  network: true\n")
    root = tmp_path / "root"
    root.mkdir()
    path = bench.network_off_config(root)
    data = yaml.safe_load(path.read_text())
    assert data["sandbox"] == {"write_paths": ["/data"], "network": False, "mode": "require"}
    assert data["model"] == {"model": "m9"}
    assert data["permissions"]["always_deny"] == ["Bash(rm *)", "WebFetch", "WebSearch"]
    assert (path.stat().st_mode & 0o777) == 0o600


def test_the_generated_config_is_accepted_by_the_settings_loader(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from nerdvana_cli.core.settings import NerdvanaSettings

    _user_config(tmp_path, monkeypatch, "")
    settings = NerdvanaSettings.load(str(bench.network_off_config(tmp_path)))
    assert (settings.sandbox.mode, settings.sandbox.network) == ("require", False)
    assert "WebFetch" in settings.permissions.always_deny


def test_a_run_without_a_user_config_still_gets_the_no_network_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NERDVANA_CONFIG", raising=False)
    monkeypatch.setattr(bench.core_paths, "user_config_path", lambda: tmp_path / "absent.yml")
    monkeypatch.setattr(bench.core_paths, "legacy_config_path", lambda: tmp_path / "absent-too.yml")
    assert "network: false" in bench.network_off_config(tmp_path).read_text()


def test_the_config_is_handed_to_the_agent(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    seen: list[list[str]] = []

    def fake_run(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        seen.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(bench.subprocess, "run", fake_run)
    bench.run_attempt(_sum_range(), 1, _options(), tmp_path, config=tmp_path / "c.yml")
    agent = next(command for command in seen if "--config" in command)
    assert agent[agent.index("--config") + 1] == str(tmp_path / "c.yml")


def test_no_network_needs_the_required_sandbox_and_a_kernel_that_can_refuse_tcp(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    assert bench.main([str(TASKS), "--no-network", "--sandbox", "auto"]) == 2
    assert "--sandbox require" in capsys.readouterr().err
    monkeypatch.setattr(bench, "landlock_abi", lambda: 3)
    assert bench.main([str(TASKS), "--no-network"]) == 2
    assert "Landlock ABI 4" in capsys.readouterr().err
    monkeypatch.setattr(bench, "landlock_abi", lambda: 4)
    assert bench.main([str(TASKS), "--no-network"]) == 0
