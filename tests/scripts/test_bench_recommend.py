"""scripts/bench_recommend.py: intervals, the choice among runs and the report.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[2] / "scripts" / "bench_recommend.py"
spec   = importlib.util.spec_from_file_location("bench_recommend", SCRIPT)
assert spec and spec.loader
rec = importlib.util.module_from_spec(spec)
sys.modules["bench_recommend"] = rec
spec.loader.exec_module(rec)


def test_the_wilson_interval_brackets_the_rate_and_narrows_with_attempts() -> None:
    low, high = rec.wilson(8, 10)
    assert low < 0.8 < high
    narrow_low, narrow_high = rec.wilson(80, 100)
    assert narrow_high - narrow_low < high - low
    assert rec.wilson(0, 0) == (0.0, 1.0)
    assert rec.wilson(10, 10)[1] == pytest.approx(1.0)
    assert rec.wilson(0, 10)[0] == pytest.approx(0.0)


def cell(label: str, attempts: int, passed: int, cost: float) -> object:
    return rec.Cell(label, attempts, passed, cost)


def test_the_cheapest_run_that_cannot_be_told_apart_from_the_best_is_chosen() -> None:
    cells = [cell("big", 40, 36, 8.0), cell("small", 40, 34, 1.0), cell("tiny", 40, 20, 0.2)]
    choice, why = rec.recommend(cells)
    assert choice.label == "small"          # 34/40 overlaps 36/40; tiny does not
    assert "within the interval" in why


def test_when_the_best_is_also_the_cheapest_it_wins() -> None:
    choice, _ = rec.recommend([cell("a", 20, 19, 1.0), cell("b", 20, 12, 3.0)])
    assert choice.label == "a"


def test_too_few_attempts_give_no_suggestion() -> None:
    choice, why = rec.recommend([cell("a", 3, 3, 0.1), cell("b", 3, 1, 0.05)])
    assert choice is None and "not enough data" in why
    choice, _ = rec.recommend([cell("a", 40, 30, 1.0)])
    assert choice is None


def test_the_pareto_front_drops_runs_beaten_on_both_counts() -> None:
    front = rec.pareto([cell("good", 10, 9, 1.0), cell("dominated", 10, 7, 2.0), cell("cheap", 10, 5, 0.1)])
    assert {c.label for c in front} == {"good", "cheap"}


def _write(path: Path, rows: list[dict]) -> Path:
    path.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    return path


def test_the_report_groups_by_tag_and_prints_a_categories_block(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    tasks = tmp_path / "tasks"
    tasks.mkdir()
    for name in ("t1", "t2"):
        (tasks / f"{name}.yml").write_text(f"id: {name}\nprompt: p\nverify: v\npath: .\ntags: [bugfix]\n", encoding="utf-8")
    rows_a = [{"task_id": "t1" if i % 2 else "t2", "passed": i < 9, "cost_usd": 0.5} for i in range(10)]
    rows_b = [{"task_id": "t1" if i % 2 else "t2", "passed": i < 8, "cost_usd": 0.05} for i in range(10)]
    a, b = _write(tmp_path / "a.jsonl", rows_a), _write(tmp_path / "b.jsonl", rows_b)
    assert rec.main([str(tasks), f"big={a}", f"small={b}", "--min-attempts", "5"]) == 0
    out = capsys.readouterr().out
    assert "bugfix" in out and "pareto" in out
    assert "agents:\n  categories:\n    bugfix: small" in out


def test_a_run_argument_without_a_label_or_file_is_refused(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (tmp_path / "t.yml").write_text("id: t\nprompt: p\nverify: v\npath: .\n", encoding="utf-8")
    assert rec.main([str(tmp_path), "no-equals-sign"]) == 2
    assert rec.main([str(tmp_path), "label=missing.jsonl"]) == 2


def test_with_no_usable_tag_the_report_says_so(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (tmp_path / "t.yml").write_text("id: t\nprompt: p\nverify: v\npath: .\ntags: [x]\n", encoding="utf-8")
    one = _write(tmp_path / "one.jsonl", [{"task_id": "t", "passed": True, "cost_usd": 0.1}])
    assert rec.main([str(tmp_path / "t.yml"), f"m={one}"]) == 0
    assert "No tag has enough data" in capsys.readouterr().out
