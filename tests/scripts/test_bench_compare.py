"""scripts/bench_compare.py: the estimators, the seeded bootstrap, the environment flag and the report.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

SCRIPTS = Path(__file__).parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import bench_compare as cmp  # noqa: E402


def attempt(task: str, passed: bool, tokens: int = 1000, cost: float = 0.1, env: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"task_id": task, "attempt": 1, "passed": passed, "cost_usd": cost, "usage": {"input_tokens": tokens}, "environment": env or {}}


def run(task: str, passes: list[bool], **kw: Any) -> list[dict[str, Any]]:
    return [attempt(task, p, **kw) for p in passes]


# ---------------------------------------------------------------------------
# pass^k as the report uses it
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("passes", "k", "expected"), [
    ([False] * 4, 2, 0.0),
    ([True] * 4,  4, 1.0),
    ([True] * 4,  9, 1.0),
    ([True, True, True, False], 2, 0.5),
    ([True, True, True, False], 9, 0.0),
])
def test_the_figures_report_pass_hat_k_with_its_edge_cases(passes: list[bool], k: int, expected: float) -> None:
    assert cmp.figures(run("t", passes), k)["pass_hat_k"] == pytest.approx(expected)


def test_the_figures_average_tokens_and_cost_per_attempt() -> None:
    rows = [attempt("t", True, tokens=1000, cost=0.2), attempt("t", False, tokens=3000, cost=0.4)]
    data = cmp.figures(rows, 1)
    assert (data["attempts"], data["passed"], data["pass_rate"]) == (2, 1, 0.5)
    assert data["mean_input_tokens"] == pytest.approx(2000)
    assert data["mean_cost_usd"] == pytest.approx(0.3)
    assert cmp.figures([], 1)["pass_rate"] == 0.0


def test_the_default_k_is_the_fewest_attempts_any_task_has() -> None:
    first  = run("a", [True] * 4) + run("b", [True] * 3)
    second = run("a", [True] * 5)
    assert cmp.default_k(first, second) == 3
    assert cmp.default_k([], []) == 1


# ---------------------------------------------------------------------------
# The bootstrap interval of the pass rate difference
# ---------------------------------------------------------------------------


def test_the_difference_is_the_mean_of_the_per_task_rate_differences() -> None:
    first  = {"a": [1, 1, 0, 0], "b": [1, 1, 1, 1]}
    second = {"a": [1, 1, 1, 0], "b": [1, 1, 1, 1], "c": [0]}
    assert cmp.rate_difference(first, second) == pytest.approx(0.125)
    assert cmp.rate_difference({}, second) == 0.0


def test_the_bootstrap_is_deterministic_for_a_seed_and_moves_with_it() -> None:
    first  = {f"t{i}": [1, 0, 1, 1] for i in range(10)}
    second = {f"t{i}": [1, 1, 1, 1] for i in range(10)}
    one    = cmp.bootstrap_difference_ci(first, second, seed=7)
    assert one == cmp.bootstrap_difference_ci(first, second, seed=7)
    assert one != cmp.bootstrap_difference_ci(first, second, seed=8)
    assert cmp.bootstrap_difference_ci(first, second) == cmp.bootstrap_difference_ci(first, second)


def test_the_interval_brackets_the_observed_difference_and_narrows_with_more_tasks() -> None:
    few_first, few_second   = {f"t{i}": [1, 0, 1, 0] for i in range(4)},  {f"t{i}": [1, 1, 1, 0] for i in range(4)}
    many_first, many_second = {f"t{i}": [1, 0, 1, 0] for i in range(40)}, {f"t{i}": [1, 1, 1, 0] for i in range(40)}
    low, high   = cmp.bootstrap_difference_ci(few_first, few_second)
    wide, narrow = high - low, cmp.bootstrap_difference_ci(many_first, many_second)
    assert low <= cmp.rate_difference(few_first, few_second) <= high
    assert narrow[1] - narrow[0] < wide


def test_identical_runs_give_a_degenerate_interval_at_zero_and_no_common_task_gives_none() -> None:
    same = {"a": [1, 1], "b": [0, 0]}
    assert cmp.bootstrap_difference_ci(same, same) == (0.0, 0.0)
    assert cmp.bootstrap_difference_ci({"a": [1]}, {"b": [1]}) == (0.0, 0.0)
    assert cmp.bootstrap_difference_ci({}, {}) == (0.0, 0.0)


def test_an_interval_from_a_clear_improvement_excludes_zero_and_one_from_noise_does_not() -> None:
    low_first  = {f"t{i}": [0, 0, 0, 0] for i in range(10)}
    high_second = {f"t{i}": [1, 1, 1, 1] for i in range(10)}
    assert cmp.bootstrap_difference_ci(low_first, high_second)[0] > 0.0
    noisy_first  = {f"t{i}": [1, 0, 1, 0] for i in range(10)}
    noisy_second = {f"t{i}": [0, 1, 0, 1] for i in range(10)}
    low, high = cmp.bootstrap_difference_ci(noisy_first, noisy_second)
    assert low <= 0.0 <= high


# ---------------------------------------------------------------------------
# The comparison and the report
# ---------------------------------------------------------------------------


def test_environments_that_differ_are_flagged_and_equal_ones_are_not() -> None:
    same = {"cpu_count": 8, "sandbox": "require"}
    assert cmp.environment_differences(same, dict(same)) == []
    differing = cmp.environment_differences(same, {"cpu_count": 4, "sandbox": "require", "isolate": True})
    assert differing == ["cpu_count: 8 against 4", "isolate: unrecorded against True"]
    assert "first run" in cmp.environment_differences({}, same)[0]
    assert "both runs" in cmp.environment_differences({}, {})[0]


def test_the_comparison_covers_each_task_overall_and_the_environment() -> None:
    first  = run("a", [True, True, False, False], tokens=4000, cost=0.4, env={"cpu_count": 8}) + run("only-first", [True])
    second = run("a", [True, True, True, False], tokens=1500, cost=0.15, env={"cpu_count": 4}) + run("only-second", [True])
    result = cmp.compare(first, second)
    assert result["k"] == 1
    assert result["tasks"]["a"]["first"]["pass_rate"] == pytest.approx(0.5)
    assert result["tasks"]["a"]["second"]["pass_rate"] == pytest.approx(0.75)
    assert result["difference"] == pytest.approx(0.25)
    assert result["common"] == 1
    assert (result["only_first"], result["only_second"]) == (["only-first"], ["only-second"])
    assert result["environment"] == ["cpu_count: 8 against 4"]
    text = cmp.render(result, ("before.jsonl", "after.jsonl"))
    for needle in ("before.jsonl", "after.jsonl", "+0.250", "95% bootstrap interval", "WARNING", "cpu_count: 8 against 4", "only in the first run"):
        assert needle in text
    assert json.dumps({key: value for key, value in result.items() if key != "interval"})


def test_main_reads_two_files_and_rejects_missing_or_empty_ones(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    before, after, empty = tmp_path / "before.jsonl", tmp_path / "after.jsonl", tmp_path / "empty.jsonl"
    before.write_text("\n".join(json.dumps(row) for row in run("a", [True, False, False, False])) + "\n", encoding="utf-8")
    after.write_text("\n".join(json.dumps(row) for row in run("a", [True, True, True, False], tokens=500)) + "\n", encoding="utf-8")
    empty.write_text("", encoding="utf-8")
    assert cmp.main([str(before), str(after)]) == 0
    out = capsys.readouterr().out
    assert "pass rate difference" in out and "mean input tokens per attempt -50.0%" in out
    assert cmp.main([str(before), str(tmp_path / "missing.jsonl")]) == 2
    assert cmp.main([str(before), str(empty)]) == 2
    capsys.readouterr()
    assert cmp.main([str(before), str(after)]) == 0
    assert capsys.readouterr().out == out
