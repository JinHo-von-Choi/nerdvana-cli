"""Unit tests for scripts/bench_symbol_tools.py: ground truth, answer checks, run and report logic.

No language server is started: the cases run against a fake executor that answers the way the tools do.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

SCRIPT = Path(__file__).parent.parent / "scripts" / "bench_symbol_tools.py"


@pytest.fixture(scope="module")
def bench() -> ModuleType:
    spec   = importlib.util.spec_from_file_location("bench_symbol_tools", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["bench_symbol_tools"] = module
    spec.loader.exec_module(module)
    return module


SOURCE = '''\
import os


@decorate
class Box:
    """A box."""

    def open(self, lid):
        return lid


def helper(a,
           b):
    value = a + b
    return value


async def tail():
    pass
'''


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "mod.py").write_text(SOURCE, encoding="utf-8")
    (tmp_path / "pkg" / "user.py").write_text("from pkg.mod import helper\nimport pkg.mod\n\nhelper(1, 2)\npkg.mod.Box().open(1)\n", encoding="utf-8")
    (tmp_path / "pkg" / "broken.py").write_text("def (:\n", encoding="utf-8")
    return tmp_path


def run(coro: Any) -> Any:
    return asyncio.run(coro)


class TestGroundTruth:
    def test_symbols_carry_the_decorator_line_the_def_line_and_the_extent(self, bench: ModuleType, project: Path) -> None:
        symbols = {s.name_path: s for s in bench.symbols_of(project / "pkg/mod.py", "pkg/mod.py")}
        assert set(symbols) == {"Box", "Box/open", "helper", "tail"}
        box = symbols["Box"]
        assert (box.kind, box.line, box.def_line, box.indent) == ("class", 4, 5, 4)
        assert symbols["Box/open"].kind == "method"
        assert (symbols["helper"].line, symbols["helper"].end_line) == (12, 15)
        assert symbols["tail"].kind == "function"

    def test_the_identifier_index_maps_uses_to_files_and_skips_unparsable_ones(self, bench: ModuleType, project: Path) -> None:
        index = bench.identifier_index(project, ("pkg",))
        assert index["helper"] == {"pkg/user.py"}
        assert index["os"] == {"pkg/mod.py"}
        assert index["Box"] == {"pkg/user.py"}
        assert "broken" not in index and all("broken" not in files for files in index.values())

    def test_a_definition_alone_is_not_a_use(self, bench: ModuleType, project: Path) -> None:
        assert "tail" not in bench.identifier_index(project, ("pkg",))

    def test_using_a_symbol_of_the_same_name_defined_in_the_module_itself_is_not_a_use(self, bench: ModuleType, project: Path) -> None:
        (project / "pkg" / "own.py").write_text("class Box:\n    pass\n\n\ndef helper():\n    return Box()\n\n\nhelper()\n", encoding="utf-8")
        (project / "pkg" / "both.py").write_text("from pkg.mod import helper\n\n\ndef helper():\n    return 1\n", encoding="utf-8")
        index = bench.identifier_index(project, ("pkg",))
        assert index["helper"] == {"pkg/user.py", "pkg/both.py"}
        assert index["Box"] == {"pkg/user.py"}

    def test_cases_are_discovered_from_the_seed_files(self, bench: ModuleType, project: Path) -> None:
        cases = bench.discover_cases(project, project, ["pkg/mod.py"], per_file=3, edits=2, scan=("pkg",))
        assert {c.tool for c in cases} == {"find_symbol", "symbol_overview", "find_referencing_symbols", "replace_symbol_body"}
        refs = [c for c in cases if c.tool == "find_referencing_symbols"]
        assert refs and all("tail" not in c.name for c in refs)


class TestAnswerChecks:
    def symbol(self, bench: ModuleType, name_path: str = "helper") -> Any:
        return bench.Symbol("pkg/mod.py", name_path.split("/")[-1], name_path, "function", 13, 13, 16, 4)

    def reply(self, bench: ModuleType, payload: Any, error: bool = False) -> Any:
        return bench.ToolOutput(payload if error else json.dumps(payload), error)

    def test_find_symbol_passes_on_name_path_file_and_line(self, bench: ModuleType) -> None:
        match = {"name_path": "helper", "location": {"file": "/abs/pkg/mod.py", "line": 13}}
        assert bench.check_find_symbol(self.reply(bench, {"matches": [match]}), self.symbol(bench)) is None

    def test_find_symbol_reports_a_wrong_line_a_missing_symbol_and_an_error(self, bench: ModuleType) -> None:
        wrong = {"name_path": "helper", "location": {"file": "/abs/pkg/mod.py", "line": 14}}
        other = {"name_path": "other", "location": {"file": "/abs/pkg/mod.py", "line": 13}}
        assert "expected 13" in bench.check_find_symbol(self.reply(bench, {"matches": [wrong]}), self.symbol(bench))
        assert "not among" in bench.check_find_symbol(self.reply(bench, {"matches": [other]}), self.symbol(bench))
        assert "reported an error" in bench.check_find_symbol(self.reply(bench, "LSP error: boom", error=True), self.symbol(bench))
        assert "not JSON" in bench.check_find_symbol(bench.ToolOutput("No symbols found", False), self.symbol(bench))

    def test_overview_scores_the_share_of_names_found(self, bench: ModuleType) -> None:
        verdict = bench.check_overview(self.reply(bench, {"symbols": [{"name": "a"}, {"name": "b"}]}), ["a", "b", "c", "d"])
        assert verdict.problem == "missing ['c', 'd']" and verdict.score == 0.5
        assert bench.check_overview(self.reply(bench, {"symbols": [{"name": "a"}]}), ["a"]).problem is None

    def test_references_score_the_share_of_files_reached(self, bench: ModuleType) -> None:
        refs    = {"references": [{"file": "/abs/pkg/user.py", "line": 1, "character": 0}]}
        verdict = bench.check_references(self.reply(bench, refs), {"pkg/user.py", "pkg/other.py"})
        assert verdict.problem == "no reference in ['pkg/other.py']" and verdict.score == 0.5
        assert bench.check_references(self.reply(bench, refs), {"pkg/user.py"}).problem is None
        assert bench.check_references(self.reply(bench, "x", error=True), {"a.py"}).score == 0.0


class TestEditJudgement:
    def symbol(self, bench: ModuleType, project: Path, name: str) -> Any:
        return next(s for s in bench.symbols_of(project / "pkg/mod.py", "pkg/mod.py") if s.name_path == name)

    def test_the_marker_goes_after_the_def_line_below_the_decorator(self, bench: ModuleType, project: Path) -> None:
        box   = self.symbol(bench, project, "Box")
        lines = SOURCE.splitlines(keepends=True)
        body  = bench.marked_body(lines, box).splitlines()
        assert body[:3] == ["@decorate", "class Box:", "    " + bench.EDIT_MARKER]

    def test_a_multi_line_signature_still_parses_with_the_marker(self, bench: ModuleType, project: Path) -> None:
        helper = self.symbol(bench, project, "helper")
        edited = bench.expected_after_edit(SOURCE, helper)
        compile(edited, "mod.py", "exec")
        assert edited.count(bench.EDIT_MARKER) == 1

    def test_the_expected_edit_is_judged_clean(self, bench: ModuleType, project: Path) -> None:
        helper = self.symbol(bench, project, "helper")
        verdict = bench.judge_edit(SOURCE, bench.expected_after_edit(SOURCE, helper), helper)
        assert verdict.problem is None and verdict.note == ""

    def test_lost_blank_lines_fail_the_edit(self, bench: ModuleType, project: Path) -> None:
        helper = self.symbol(bench, project, "helper")
        result = bench.expected_after_edit(SOURCE, helper).replace("    return value\n\n\n", "    return value\n")
        verdict = bench.judge_edit(SOURCE, result, helper)
        assert verdict.problem is not None and "blank lines" in verdict.problem

    def test_a_changed_tree_a_syntax_error_and_a_missing_marker_fail(self, bench: ModuleType, project: Path) -> None:
        helper = self.symbol(bench, project, "helper")
        good   = bench.expected_after_edit(SOURCE, helper)
        assert "syntax tree" in bench.judge_edit(SOURCE, good.replace("a + b", "a - b"), helper).problem
        assert "no longer parses" in bench.judge_edit(SOURCE, good + "def (:\n", helper).problem
        assert "0 times" in bench.judge_edit(SOURCE, SOURCE, helper).problem


class TestRunAndReport:
    def case(self, bench: ModuleType, name: str, tool: str, verdict: Any = None, delay: float = 0.0, error: Exception | None = None) -> Any:
        async def check(execute: Any) -> Any:
            if delay:
                await asyncio.sleep(delay)
            if error is not None:
                raise error
            return verdict or bench.Verdict()

        return bench.Case(name, tool, check)

    async def execute(self, name: str, arguments: dict[str, Any]) -> Any:
        raise AssertionError("not called")

    def test_a_passing_case_records_its_latency(self, bench: ModuleType) -> None:
        outcome = run(bench.run_case(self.case(bench, "a", "find_symbol", delay=0.01), self.execute, 1, 5.0))
        assert outcome.ok and outcome.problem == "" and outcome.latency_ms >= 10 and outcome.run == 1

    def test_a_problem_an_exception_and_a_timeout_are_failures(self, bench: ModuleType) -> None:
        failing = run(bench.run_case(self.case(bench, "a", "find_symbol", bench.Verdict("wrong")), self.execute, 1, 5.0))
        raising = run(bench.run_case(self.case(bench, "b", "find_symbol", error=KeyError("boom")), self.execute, 1, 5.0))
        slow    = run(bench.run_case(self.case(bench, "c", "find_symbol", delay=1.0), self.execute, 1, 0.01))
        assert (failing.ok, failing.problem) == (False, "wrong")
        assert not raising.ok and "KeyError" in raising.problem
        assert not slow.ok and "no answer within" in slow.problem

    def test_every_case_runs_repeat_times_in_order(self, bench: ModuleType) -> None:
        cases    = [self.case(bench, "a", "find_symbol"), self.case(bench, "b", "symbol_overview")]
        outcomes = run(bench.run_all(cases, self.execute, 2, 5.0))
        assert [(o.name, o.run) for o in outcomes] == [("a", 1), ("a", 2), ("b", 1), ("b", 2)]

    def outcomes(self, bench: ModuleType) -> list[Any]:
        make = bench.Outcome
        return [
            make("a", "find_symbol", 1, True, 10.0, ""), make("a", "find_symbol", 2, True, 30.0, ""),
            make("b", "find_symbol", 1, False, 20.0, "wrong"),
            make("c", "find_referencing_symbols", 1, False, 5.0, "none", score=0.25),
            make("c", "find_referencing_symbols", 2, True, 7.0, "", score=1.0),
            make("d", "replace_symbol_body", 1, True, 100.0, "", note="blank lines"),
        ]

    def test_the_summary_has_rates_latencies_scores_and_notes_per_tool(self, bench: ModuleType) -> None:
        summary = bench.summarize(self.outcomes(bench))
        finds   = summary["by_tool"]["find_symbol"]
        assert (finds["runs"], finds["passed"], finds["success_rate"]) == (3, 2, 0.6667)
        assert (finds["mean_ms"], finds["p50_ms"], finds["max_ms"]) == (20.0, 20.0, 30.0)
        assert "mean_score" not in finds
        assert summary["by_tool"]["find_referencing_symbols"]["mean_score"] == 0.625
        assert summary["by_tool"]["replace_symbol_body"]["notes"] == {"blank lines": 1}
        assert summary["overall"]["runs"] == 6 and summary["overall"]["passed"] == 4
        assert "symbol_overview" not in summary["by_tool"]

    def test_an_empty_run_has_a_zero_rate_and_no_division_error(self, bench: ModuleType) -> None:
        summary = bench.summarize([])
        assert summary["overall"]["success_rate"] == 0.0 and summary["by_tool"] == {}

    def test_the_report_lists_failures_and_is_json(self, bench: ModuleType) -> None:
        report = bench.build_report(self.outcomes(bench), "/bin/pyright-langserver", repository="/repo")
        json.dumps(report)
        assert report["status"] == "ok" and report["server"] == "/bin/pyright-langserver"
        assert [f["name"] for f in report["failures"]] == ["b", "c"]
        assert len(report["runs"]) == 6

    def test_the_exit_code_tells_skipped_failed_and_clean_apart(self, bench: ModuleType) -> None:
        assert bench.exit_code(bench.build_report([], "", "skipped", "no pyright")) == 2
        assert bench.exit_code(bench.build_report(self.outcomes(bench), "s")) == 1
        clean = [o for o in self.outcomes(bench) if o.ok]
        assert bench.exit_code(bench.build_report(clean, "s")) == 0

    def test_without_pyright_the_script_writes_a_skipped_report(self, bench: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        monkeypatch.setattr(bench.shutil, "which", lambda name: None)
        out  = tmp_path / "report.json"
        code = bench.main(["--out", str(out)])
        report = json.loads(out.read_text(encoding="utf-8"))
        assert code == 2 and report["status"] == "skipped" and "pyright-langserver" in report["reason"]
        assert json.loads(capsys.readouterr().out)["status"] == "skipped"
