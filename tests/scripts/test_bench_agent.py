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

SCRIPT  = Path(__file__).parents[2] / "scripts" / "bench_agent.py"
TASKS   = Path(__file__).parents[2] / "benchmarks" / "tasks"


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
    return argparse.Namespace(
        approval_mode="yolo", sandbox="require", gate=False, set=[], model="", provider="",
        isolate=False, no_network=False,
        method=bench.BenchMethod.parse("single"), substrate=bench.BenchSubstrate.parse("loop"),
    )


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
    command = bench.agent_command(task, argparse.Namespace(approval_mode="plan", sandbox="require", gate=False, set=[], model="m1", provider="anthropic"))
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


def test_signals_and_stop_reasons_are_split_by_whether_the_attempt_passed() -> None:
    attempts = [
        bench.Attempt("a", 1, True,  signals={"new_diagnostics": 1}),
        bench.Attempt("a", 2, False, stop="error_max_turns", signals={"cas_rejected": 2, "new_diagnostics": 3}),
        bench.Attempt("b", 1, False, stop="error_provider"),
    ]
    summary = bench.summarize(attempts, 2)
    assert summary["signals_in_failed_attempts"] == {"cas_rejected": 2, "new_diagnostics": 3}
    assert summary["signals_in_passed_attempts"] == {"new_diagnostics": 1}
    assert summary["stop_reasons"] == {"error_max_turns": 1, "error_provider": 1}
    text = bench.render(summary)
    assert "cas_rejected" in text and "how failed attempts ended" in text


def test_the_run_result_signals_are_read_from_the_agent_output(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    script  = "import json\nprint(json.dumps({'type': 'result', 'subtype': 'success', 'num_turns': 1, 'total_cost_usd': 0.0, 'signals': {'cas_rejected': 2}}))\n"
    attempt = _run(monkeypatch, tmp_path, script)
    assert attempt.signals == {"cas_rejected": 2}  # type: ignore[attr-defined]


def test_the_gate_flag_passes_the_tasks_verify_command_to_the_agent() -> None:
    task  = bench.Task("t", "do it", "python check.py", path="x")
    plain = bench.agent_command(task, argparse.Namespace(approval_mode="yolo", sandbox="require", gate=False, set=[], model="", provider=""))
    gated = bench.agent_command(task, argparse.Namespace(approval_mode="yolo", sandbox="require", gate=True, set=[], model="", provider=""))
    assert "--verify" not in plain
    assert gated[gated.index("--verify") + 1] == "python check.py"


def test_token_totals_are_summed_from_each_attempts_usage() -> None:
    attempts = [
        bench.Attempt("a", 1, True,  usage={"input_tokens": 1000, "output_tokens": 50, "cache_read_tokens": 800}),
        bench.Attempt("a", 2, False, usage={"input_tokens": 500, "output_tokens": 20}),
    ]
    summary = bench.summarize(attempts, 2)
    assert summary["tokens"] == {"input_tokens": 1500, "output_tokens": 70, "cache_read_tokens": 800, "cache_write_tokens": 0}
    assert "1,500 in (800 from cache)" in bench.render(summary)


def test_set_overrides_are_passed_to_every_attempt() -> None:
    task    = bench.Task("t", "do it", "v", path="x")
    command = bench.agent_command(task, argparse.Namespace(approval_mode="yolo", sandbox="require", gate=False, set=["session.compact_threshold=0.5", "session.max_turns=9"], model="", provider=""))
    pairs   = [command[i + 1] for i, word in enumerate(command) if word == "--set"]
    assert pairs == ["session.compact_threshold=0.5", "session.max_turns=9"]


# ---------------------------------------------------------------------------
# The three axes of a run
# ---------------------------------------------------------------------------


def test_the_method_decides_how_many_candidates_an_attempt_produces(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    seen: list[int] = []
    monkeypatch.setattr(bench, "agent_command", lambda t, o: [sys.executable, "-c", IDLER])
    monkeypatch.setattr(bench, "run_candidate", lambda task, number, index, candidates, *a, **k: seen.append(index) or bench.Attempt(task.id, number, False))
    options = _options()
    options.method = bench.BenchMethod.parse("ppc:3")

    attempt = bench.run_attempt(_sum_range(), 1, options, tmp_path)

    assert seen == [1, 2, 3]
    assert attempt.candidates == 3  # type: ignore[attr-defined]


def test_the_attempt_is_judged_by_the_best_of_its_candidates(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def fake(task, number, index, candidates, *a, **k):
        return bench.Attempt(task.id, number, passed=index == 2)

    monkeypatch.setattr(bench, "run_candidate", fake)
    options = _options()
    options.method = bench.BenchMethod.parse("ppc:3")

    attempt = bench.run_attempt(_sum_range(), 1, options, tmp_path)

    assert attempt.passed is True  # type: ignore[attr-defined]


def test_the_cost_of_an_attempt_is_what_all_of_its_candidates_spent(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def fake(task, number, index, candidates, *a, **k):
        return bench.Attempt(task.id, number, passed=False, cost_usd=0.5)

    monkeypatch.setattr(bench, "run_candidate", fake)
    options = _options()
    options.method = bench.BenchMethod.parse("ppc:2")

    attempt = bench.run_attempt(_sum_range(), 1, options, tmp_path)

    assert attempt.cost_usd == 1.0  # type: ignore[attr-defined]


def test_the_run_reports_the_cell_of_the_experiment_it_is(capsys: pytest.CaptureFixture[str]) -> None:
    assert bench.main([str(TASKS), "--method", "ppc:2", "--substrate", "subagent:code-reviewer"]) == 0

    out = capsys.readouterr().out
    assert "method: ppc:2" in out
    assert "substrate: subagent:code-reviewer" in out
    assert "runmark:" in out


def test_a_results_directory_made_with_another_configuration_is_not_continued(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    results = tmp_path / "results"
    results.mkdir()
    (results / "runmark").write_text("something-else\n", encoding="utf-8")

    assert bench.main([str(TASKS), "--results-dir", str(results)]) == 2
    assert "not continued with another configuration" in capsys.readouterr().err


def test_the_same_configuration_continues_its_results_directory(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    results = tmp_path / "results"
    results.mkdir()

    assert bench.main([str(TASKS), "--results-dir", str(results)]) == 0
    assert "dry run" in capsys.readouterr().out


def test_the_network_of_an_evaluation_refuses_the_hosts_that_hold_the_answers(tmp_path: Path) -> None:
    config = bench.network_off_config(tmp_path)

    import yaml

    data = yaml.safe_load(config.read_text(encoding="utf-8"))
    assert data["sandbox"]["network"] == "allowlist"
    assert "github.com" not in data["sandbox"]["allowed_domains"]
    assert "pypi.org" in data["sandbox"]["allowed_domains"]


# ---------------------------------------------------------------------------
# Where the results go
# ---------------------------------------------------------------------------


def _no_model(monkeypatch: pytest.MonkeyPatch) -> None:
    """Answer every task without a model, so a run may be exercised for its plumbing."""
    monkeypatch.setattr(bench, "run_attempt", lambda task, number, options, root, **k: bench.Attempt(task.id, number, True))
    monkeypatch.setattr(bench, "run_environment", lambda options: {})


def test_the_results_of_a_run_land_inside_its_results_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _no_model(monkeypatch)
    results = tmp_path / "results"

    assert bench.main([str(TASKS), "--results-dir", str(results), "--yes"]) == 0

    assert (results / "results.jsonl").is_file()
    assert (results / "runmark").is_file()


def test_a_run_without_a_results_directory_writes_no_mark_into_the_working_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _no_model(monkeypatch)
    monkeypatch.chdir(tmp_path)

    assert bench.main([str(TASKS), "--yes"]) == 0

    assert not (tmp_path / "runmark").exists()
    assert (tmp_path / "bench-results.jsonl").is_file()


def test_the_results_directory_is_not_contaminated_by_a_run_of_another_configuration(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _no_model(monkeypatch)
    results = tmp_path / "results"

    assert bench.main([str(TASKS), "--results-dir", str(results), "--yes"]) == 0
    assert bench.main([str(TASKS), "--results-dir", str(results), "--method", "ppc:2", "--yes"]) == 2


def _runmark_of(output: str) -> str:
    """The mark the run printed."""
    for line in output.splitlines():
        if "runmark:" in line:
            return line.split("runmark:")[1].strip()
    return ""


def test_the_same_tasks_read_from_two_places_are_two_different_runs(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    first  = tmp_path / "a"
    second = tmp_path / "b"
    for directory in (first, second):
        directory.mkdir()
        for task in sorted(TASKS.glob("*.yml")):
            (directory / task.name).write_text(task.read_text(encoding="utf-8"), encoding="utf-8")

    assert bench.main([str(first)]) == 0
    one = _runmark_of(capsys.readouterr().out)
    assert bench.main([str(second)]) == 0
    two = _runmark_of(capsys.readouterr().out)

    assert one and two
    assert one != two


# ---------------------------------------------------------------------------
# The sub-agent substrate
# ---------------------------------------------------------------------------


def _fake_subagent(monkeypatch: pytest.MonkeyPatch, seen: dict[str, object]) -> None:
    """Answer with a preset without calling a model."""
    import nerdvana_cli.cli.bootstrap as bootstrap
    import nerdvana_cli.core.delegation.subagent as subagent_module

    class FakeSub:
        cost_usd    = 0.25
        stopped_for = "completed"

    def fake_build(settings, definition, prompt, agent_id, category):
        seen["settings"]    = settings
        seen["definition"] = definition
        seen["prompt"]     = prompt
        seen["agent_id"]   = agent_id
        return FakeSub()

    async def fake_run(config, abort):
        return "the answer", 0

    monkeypatch.setattr(bootstrap, "build_subagent", fake_build)
    monkeypatch.setattr(subagent_module, "run_subagent", fake_run)


def test_a_sub_agent_preset_answers_the_task(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    seen: dict[str, object] = {}
    _fake_subagent(monkeypatch, seen)
    options = _options()
    options.substrate = bench.BenchSubstrate.parse("subagent:code-reviewer")
    task = bench.load_tasks(TASKS)[0]

    attempt = bench.run_subagent_candidate(task, 1, 1, options, tmp_path)

    assert seen["definition"].agent_type == "code-reviewer"  # type: ignore[union-attr]
    assert seen["prompt"] == task.prompt
    assert seen["settings"].cwd == str(tmp_path / f"{task.id}-1-c1")  # type: ignore[union-attr]
    assert attempt.cost_usd == 0.25
    assert attempt.error == ""


def test_the_provider_of_the_run_reaches_the_sub_agent(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    seen: dict[str, object] = {}
    _fake_subagent(monkeypatch, seen)
    options = _options()
    options.substrate = bench.BenchSubstrate.parse("subagent:code-reviewer")
    options.provider   = "anthropic"
    options.model      = "claude-sonnet-4"
    task = bench.load_tasks(TASKS)[0]

    bench.run_subagent_candidate(task, 1, 1, options, tmp_path)

    settings = seen["settings"]
    assert settings.model.provider == "anthropic"  # type: ignore[union-attr]
    assert settings.model.model == "claude-sonnet-4"  # type: ignore[union-attr]


def test_a_preset_that_does_not_exist_is_reported(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    seen: dict[str, object] = {}
    _fake_subagent(monkeypatch, seen)
    options = _options()
    options.substrate = bench.BenchSubstrate.parse("subagent:no-such-preset")
    task = bench.load_tasks(TASKS)[0]

    attempt = bench.run_subagent_candidate(task, 1, 1, options, tmp_path)

    assert "no sub-agent preset named 'no-such-preset'" in attempt.error
    assert "settings" not in seen
