"""The three axes of a benchmark run: the method, the substrate and the benchmark.

Author: 최진호
Date:   2026-10-21
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from nerdvana_cli.core.harness.bench_spec import (
    BenchBenchmark,
    BenchError,
    BenchMethod,
    BenchSpec,
    BenchSubstrate,
    BenchTask,
    RunmarkMismatchError,
    check_runmark,
    domain_of,
    evaluation_network,
    load_benchmark,
    read_runmark,
    split_by_domain,
    write_runmark,
)

# ---------------------------------------------------------------------------
# The method
# ---------------------------------------------------------------------------


def test_the_method_is_one_run_or_k_candidates() -> None:
    assert BenchMethod.parse("single").candidates == 1
    assert BenchMethod.parse("ppc").candidates == 1
    assert BenchMethod.parse("ppc:2").candidates == 2
    assert BenchMethod.parse("ppc:3").candidates == 3


def test_a_method_that_is_not_one_of_the_two_is_refused() -> None:
    with pytest.raises(BenchError):
        BenchMethod.parse("magic")


def test_a_method_with_a_k_out_of_range_is_refused() -> None:
    with pytest.raises(BenchError):
        BenchMethod.parse("ppc:4")
    with pytest.raises(BenchError):
        BenchMethod.parse("ppc:0")


def test_a_method_whose_k_is_not_a_number_is_refused() -> None:
    with pytest.raises(BenchError):
        BenchMethod.parse("ppc:two")


# ---------------------------------------------------------------------------
# The substrate
# ---------------------------------------------------------------------------


def test_the_substrate_is_the_loop_or_a_sub_agent_preset() -> None:
    assert BenchSubstrate.parse("loop").preset == ""
    assert BenchSubstrate.parse("subagent:code-reviewer").preset == "code-reviewer"


def test_a_substrate_that_is_not_one_of_the_two_is_refused() -> None:
    with pytest.raises(BenchError):
        BenchSubstrate.parse("cloud")


# ---------------------------------------------------------------------------
# The benchmark
# ---------------------------------------------------------------------------


def test_the_benchmarks_of_the_repository_can_be_named() -> None:
    benchmark = load_benchmark("short-20", Path(__file__).parents[2])

    assert benchmark.name == "short-20"
    assert len(benchmark.tasks) == 20
    assert all(task.prompt for task in benchmark.tasks)


def test_the_long_benchmark_can_be_named() -> None:
    benchmark = load_benchmark("long-8", Path(__file__).parents[2])

    assert len(benchmark.tasks) == 8


def test_a_benchmark_that_is_not_known_is_refused() -> None:
    with pytest.raises(BenchError):
        load_benchmark("nope", Path(__file__).parents[2])


def test_an_external_benchmark_needs_to_be_told_where_its_tasks_are() -> None:
    with pytest.raises(BenchError):
        load_benchmark("workbuddy", Path(__file__).parents[2])


def test_the_tasks_of_an_external_benchmark_are_read_in_its_own_format(tmp_path: Path) -> None:
    source = tmp_path / "workbuddy.json"
    source.write_text(json.dumps({
        "tasks": [
            {"id": "wb-1", "instruction": "fix it", "test_command": "pytest", "tags": ["python"]},
        ],
    }), encoding="utf-8")

    benchmark = load_benchmark("workbuddy", Path(__file__).parents[2], str(tmp_path))

    assert benchmark.tasks[0].id == "wb-1"
    assert benchmark.tasks[0].prompt == "fix it"
    assert benchmark.tasks[0].verify == "pytest"


# ---------------------------------------------------------------------------
# The mark of a run
# ---------------------------------------------------------------------------


def _spec(**kwargs: object) -> BenchSpec:
    return BenchSpec(
        method    = BenchMethod.parse("single"),
        substrate = BenchSubstrate.parse("loop"),
        benchmark = load_benchmark("short-20", Path(__file__).parents[2]),
        **kwargs,  # type: ignore[arg-type]
    )


def test_the_same_cell_and_the_same_settings_have_the_same_mark() -> None:
    spec = _spec()

    assert spec.runmark({"model": "claude"}) == spec.runmark({"model": "claude"})


def test_a_different_cell_has_a_different_mark() -> None:
    single = _spec()
    ppc    = BenchSpec(
        method    = BenchMethod.parse("ppc:2"),
        substrate = BenchSubstrate.parse("loop"),
        benchmark = single.benchmark,
    )

    assert single.runmark() != ppc.runmark()


def test_a_different_setting_has_a_different_mark() -> None:
    spec = _spec()

    assert spec.runmark({"model": "claude"}) != spec.runmark({"model": "gpt"})


def test_the_tasks_come_from_a_place_and_the_place_is_part_of_the_mark(tmp_path: Path) -> None:
    benchmark = load_benchmark("short-20", Path(__file__).parents[2])
    elsewhere = BenchBenchmark(
        name    = benchmark.name,
        tasks   = benchmark.tasks,
        adapter = benchmark.adapter,
        source  = str(tmp_path),
    )
    spec    = BenchSpec(method=BenchMethod.parse("single"), substrate=BenchSubstrate.parse("loop"), benchmark = benchmark)
    other   = BenchSpec(method=BenchMethod.parse("single"), substrate=BenchSubstrate.parse("loop"), benchmark = elsewhere)

    assert spec.runmark() != other.runmark()


def test_the_same_tasks_from_the_same_place_have_the_same_mark() -> None:
    first  = load_benchmark("short-20", Path(__file__).parents[2])
    second = load_benchmark("short-20", Path(__file__).parents[2])

    assert first.source == second.source
    assert BenchSpec(method=BenchMethod.parse("single"), substrate=BenchSubstrate.parse("loop"), benchmark = first).runmark() \
        == BenchSpec(method=BenchMethod.parse("single"), substrate=BenchSubstrate.parse("loop"), benchmark = second).runmark()


def test_a_results_directory_is_not_continued_with_another_configuration(tmp_path: Path) -> None:
    write_runmark(tmp_path, "aaaa")

    check_runmark(tmp_path, "aaaa")

    with pytest.raises(RunmarkMismatchError):
        check_runmark(tmp_path, "bbbb")


def test_a_directory_without_a_mark_is_a_new_one(tmp_path: Path) -> None:
    check_runmark(tmp_path, "aaaa")

    assert read_runmark(tmp_path) == ""


def test_the_mark_of_a_directory_is_read_back(tmp_path: Path) -> None:
    write_runmark(tmp_path, "abcd")

    assert read_runmark(tmp_path) == "abcd"


# ---------------------------------------------------------------------------
# The network of an evaluation
# ---------------------------------------------------------------------------


def test_the_hosts_that_hold_the_answers_are_not_reachable() -> None:
    allowed = evaluation_network()["allowed_domains"]

    assert "huggingface.co" not in allowed
    assert "github.com" not in allowed
    assert "raw.githubusercontent.com" not in allowed


def test_the_package_registries_stay_reachable() -> None:
    allowed = evaluation_network()["allowed_domains"]

    assert "pypi.org" in allowed
    assert "registry.npmjs.org" in allowed


def test_the_network_of_an_evaluation_is_an_allowlist() -> None:
    network = evaluation_network()

    assert network["mode"] == "require"
    assert network["network"] == "allowlist"


# ---------------------------------------------------------------------------
# The split
# ---------------------------------------------------------------------------


def _tasks(count: int, domain: str) -> list[BenchTask]:
    return [BenchTask(id=f"{domain}-{index}", tags=(domain,)) for index in range(count)]


def test_every_domain_keeps_a_share_on_both_sides() -> None:
    tasks = _tasks(6, "python") + _tasks(4, "node")

    split = split_by_domain(tasks, test_ratio=0.3)

    assert {task.stratum for task in split.train} == {"python", "node"}
    assert {task.stratum for task in split.test} == {"python", "node"}


def test_the_two_sides_hold_the_tasks_between_them() -> None:
    tasks = _tasks(6, "python") + _tasks(4, "node")

    split = split_by_domain(tasks)

    held = {task.id for task in split.train} | {task.id for task in split.test}
    assert held == {task.id for task in tasks}
    assert not ({task.id for task in split.train} & {task.id for task in split.test})


def test_a_domain_with_one_task_goes_to_the_train_side() -> None:
    split = split_by_domain(_tasks(1, "c") + _tasks(4, "python"))

    assert [task.id for task in split.train if task.stratum == "c"] == ["c-0"]
    assert [task.id for task in split.test  if task.stratum == "c"] == []


def test_the_same_tasks_and_seed_always_give_the_same_split() -> None:
    tasks = _tasks(8, "python")

    first  = split_by_domain(tasks, seed=3)
    second = split_by_domain(tasks, seed=3)

    assert first.to_dict() == second.to_dict()


def test_a_share_outside_the_range_is_refused() -> None:
    with pytest.raises(BenchError):
        split_by_domain(_tasks(4, "python"), test_ratio=1.5)


def test_the_domain_of_a_task_is_the_language_it_is_about() -> None:
    assert domain_of(["bugfix", "python"]) == "python"
    assert domain_of(["node", "bugfix"]) == "node"
    assert domain_of(["bugfix"]) == "default"
    assert domain_of(["bugfix"], "explicit") == "explicit"
