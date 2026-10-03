"""scripts/bench_agent.py: task parsing, statistics and the run plumbing (no model is called).

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT  = Path(__file__).parent.parent / "scripts" / "bench_agent.py"
TASKS   = Path(__file__).parent.parent / "benchmarks" / "tasks"


def _load() -> ModuleType:
    spec   = importlib.util.spec_from_file_location("bench_agent", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["bench_agent"] = module
    spec.loader.exec_module(module)
    return module


bench = _load()


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("n", "c", "k", "expected"), [
    (1, 1, 1, 1.0),
    (1, 0, 1, 0.0),
    (4, 2, 1, 0.5),
    (4, 2, 2, 1 - 1 / 6),
    (4, 0, 4, 0.0),
    (4, 3, 2, 1.0),
    (3, 1, 10, 1.0),
    (0, 0, 1, 0.0),
])
def test_pass_at_k_follows_the_unbiased_estimator(n: int, c: int, k: int, expected: float) -> None:
    assert bench.pass_at_k(n, c, k) == pytest.approx(expected)


def test_the_summary_totals_cost_and_counts_solved_tasks() -> None:
    attempts = [
        bench.Attempt("a", 1, True,  cost_usd=0.10, duration_s=10),
        bench.Attempt("a", 2, False, cost_usd=0.30, duration_s=30),
        bench.Attempt("b", 1, False, cost_usd=0.20, duration_s=20),
    ]
    summary = bench.summarize(attempts, 2)
    by_task = {t["task"]: t for t in summary["tasks"]}
    assert by_task["a"]["pass_at_1"] == pytest.approx(0.5)
    assert by_task["a"]["pass_at_2"] == pytest.approx(1.0)
    assert by_task["a"]["mean_seconds"] == pytest.approx(20)
    assert summary["tasks_solved"] == 1
    assert summary["total_cost_usd"] == pytest.approx(0.6)
    assert summary["cost_per_solved_task"] == pytest.approx(0.6)


def test_nothing_solved_leaves_cost_per_solved_task_undefined() -> None:
    summary = bench.summarize([bench.Attempt("a", 1, False, cost_usd=1.0)], 1)
    assert summary["cost_per_solved_task"] is None
    assert "n/a" in bench.render(summary)


# ---------------------------------------------------------------------------
# Task files
# ---------------------------------------------------------------------------


def _sum_range() -> object:
    return next(task for task in bench.load_tasks(TASKS) if task.id == "sum-range")


def test_the_shipped_task_loads_with_a_resolved_fixture_path() -> None:
    task = _sum_range()
    assert task.id == "sum-range"  # type: ignore[attr-defined]
    assert (Path(task.path) / "calc.py").is_file()  # type: ignore[attr-defined]


@pytest.mark.parametrize("data", [
    {"prompt": "p", "verify": "v", "path": "x"},
    {"id": "t", "verify": "v", "path": "x"},
    {"id": "t", "prompt": "p", "path": "x"},
    {"id": "t", "prompt": "p", "verify": "v"},
    {"id": "t", "prompt": "p", "verify": "v", "path": "x", "repo": "y"},
    "not a mapping",
])
def test_incomplete_task_files_are_rejected(data: object, tmp_path: Path) -> None:
    with pytest.raises(bench.TaskError):
        bench.parse_task(data, tmp_path)


def test_duplicate_ids_and_empty_directories_are_rejected(tmp_path: Path) -> None:
    with pytest.raises(bench.TaskError):
        bench.load_tasks(tmp_path)
    for name in ("a", "b"):
        (tmp_path / f"{name}.yml").write_text("id: same\nprompt: p\nverify: v\npath: .\n", encoding="utf-8")
    with pytest.raises(bench.TaskError):
        bench.load_tasks(tmp_path)


def test_the_worst_case_spend_is_the_sum_of_the_ceilings_times_attempts() -> None:
    tasks = [bench.Task("a", "p", "v", path="x", max_cost_usd=0.5), bench.Task("b", "p", "v", path="x", max_cost_usd=1.5)]
    assert bench.worst_case_cost(tasks, 3) == pytest.approx(6.0)


def test_the_result_object_is_found_among_other_output() -> None:
    out = 'noise\n{"type": "assistant"}\n{"type": "result", "num_turns": 3}\n'
    assert bench.parse_result(out) == {"type": "result", "num_turns": 3}
    assert bench.parse_result("nothing here") == {}


# ---------------------------------------------------------------------------
# Running one attempt with a stand-in agent
# ---------------------------------------------------------------------------

FIXER = (
    "import json, pathlib\n"
    "p = pathlib.Path('calc.py'); p.write_text(p.read_text().replace('range(start, stop)', 'range(start, stop + 1)'))\n"
    "print(json.dumps({'type': 'result', 'subtype': 'success', 'num_turns': 2, 'total_cost_usd': 0.25}))\n"
)
IDLER = "import json\nprint(json.dumps({'type': 'result', 'subtype': 'success', 'num_turns': 1, 'total_cost_usd': 0.1}))\n"


def _options() -> argparse.Namespace:
    return argparse.Namespace(approval_mode="yolo", sandbox="require", model="", provider="")


def _run(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, script: str) -> object:
    task = _sum_range()
    monkeypatch.setattr(bench, "agent_command", lambda t, o: [sys.executable, "-c", script])
    return bench.run_attempt(task, 1, _options(), tmp_path)


def test_a_fixing_agent_passes_the_verify_command_and_the_fixture_stays_untouched(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    before  = (TASKS.parent / "fixtures" / "sum-range" / "calc.py").read_text()
    attempt = _run(monkeypatch, tmp_path, FIXER)
    assert (attempt.passed, attempt.stop, attempt.turns, attempt.cost_usd) == (True, "success", 2, 0.25)  # type: ignore[attr-defined]
    assert (TASKS.parent / "fixtures" / "sum-range" / "calc.py").read_text() == before


def test_an_agent_that_changes_nothing_fails_even_though_it_reports_success(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    attempt = _run(monkeypatch, tmp_path, IDLER)
    assert attempt.passed is False  # type: ignore[attr-defined]
    assert attempt.stop == "success"  # type: ignore[attr-defined]


def test_output_without_a_result_object_is_recorded_as_an_error(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    attempt = _run(monkeypatch, tmp_path, "print('crashed')")
    assert attempt.passed is False  # type: ignore[attr-defined]
    assert "no result object" in attempt.error  # type: ignore[attr-defined]


def test_the_agent_command_carries_the_ceilings_and_the_chosen_model() -> None:
    task    = bench.Task("t", "do it", "v", path="x", max_turns=7, max_cost_usd=0.4)
    command = bench.agent_command(task, argparse.Namespace(approval_mode="plan", sandbox="require", model="m1", provider="anthropic"))
    assert command[command.index("--max-turns") + 1] == "7"
    assert command[command.index("--max-cost-usd") + 1] == "0.4"
    assert command[command.index("--model") + 1] == "m1"
    assert command[command.index("--approval-mode") + 1] == "plan"
    assert command[command.index("--sandbox") + 1] == "require"
    assert json.dumps(command)  # plain strings only


def test_without_yes_nothing_runs(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    called: list[object] = []
    monkeypatch.setattr(bench, "run_attempt", lambda *a, **k: called.append(a))
    assert bench.main([str(TASKS), "--attempts", "2"]) == 0
    assert called == []
    assert "dry run" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# Tags and the bootstrap interval
# ---------------------------------------------------------------------------


def test_the_interval_is_deterministic_contains_the_mean_and_narrows_with_more_tasks() -> None:
    few  = [1.0, 0.0, 1.0, 1.0]
    many = few * 10
    low, high = bench.bootstrap_ci(few)
    assert (low, high) == bench.bootstrap_ci(few)
    assert low <= sum(few) / len(few) <= high
    wide, narrow = high - low, bench.bootstrap_ci(many)[1] - bench.bootstrap_ci(many)[0]
    assert narrow < wide
    assert bench.bootstrap_ci([]) == (0.0, 0.0)
    assert bench.bootstrap_ci([1.0, 1.0, 1.0]) == (1.0, 1.0)


def test_the_summary_groups_pass_rates_by_tag_and_reports_the_interval() -> None:
    attempts = [bench.Attempt("a", 1, True), bench.Attempt("b", 1, False), bench.Attempt("c", 1, True)]
    summary  = bench.summarize(attempts, 1, {"a": ("python", "bugfix"), "b": ("python",), "c": ("node",)})
    assert summary["by_tag"]["python"] == {"tasks": 2, "mean_pass_at_1": pytest.approx(0.5)}
    assert summary["by_tag"]["node"]["mean_pass_at_1"] == pytest.approx(1.0)
    low, high = summary["mean_pass_at_1_ci"]
    assert 0.0 <= low <= summary["mean_pass_at_1"] <= high <= 1.0
    assert "bootstrap interval" in bench.render(summary)


def test_task_tags_are_read_and_the_tag_filter_selects_tasks(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    tasks = bench.load_tasks(TASKS)
    assert all(t.tags for t in tasks)
    assert bench.main([str(TASKS), "--tag", "injection"]) == 0
    assert "1 task(s)" in capsys.readouterr().out
    assert bench.main([str(TASKS), "--tag", "no-such-tag"]) == 2


def test_a_failed_run_keeps_the_error_text_the_agent_reported(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    script  = "import json\nprint(json.dumps({'type': 'result', 'subtype': 'error_provider', 'is_error': True, 'result': 'openai package not installed'}))\n"
    attempt = _run(monkeypatch, tmp_path, script)
    assert attempt.passed is False  # type: ignore[attr-defined]
    assert "openai package not installed" in attempt.error  # type: ignore[attr-defined]
