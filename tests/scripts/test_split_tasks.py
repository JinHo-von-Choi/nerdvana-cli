"""scripts/split_tasks.py: the split of a benchmark into train and test.

Author: 최진호
Date:   2026-10-21
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT = Path(__file__).parents[2] / "scripts" / "split_tasks.py"
TASKS  = Path(__file__).parents[2] / "benchmarks" / "tasks"


def _load() -> ModuleType:
    spec   = importlib.util.spec_from_file_location("split_tasks", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["split_tasks"] = module
    spec.loader.exec_module(module)
    return module


split = _load()


def test_the_tasks_are_split_into_train_and_test(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert split.main([str(TASKS), "--out", str(tmp_path)]) == 0

    train = json.loads((tmp_path / "train.json").read_text(encoding="utf-8"))
    test  = json.loads((tmp_path / "test.json").read_text(encoding="utf-8"))
    assert train["tasks"] and test["tasks"]
    assert not ({task["id"] for task in train["tasks"]} & {task["id"] for task in test["tasks"]})


def test_every_domain_is_represented_on_both_sides(tmp_path: Path) -> None:
    split.main([str(TASKS), "--out", str(tmp_path)])

    train = json.loads((tmp_path / "train.json").read_text(encoding="utf-8"))
    test  = json.loads((tmp_path / "test.json").read_text(encoding="utf-8"))
    assert set(train["domains"]) & set(test["domains"])


def test_the_same_seed_gives_the_same_split(tmp_path: Path) -> None:
    first  = tmp_path / "a"
    second = tmp_path / "b"
    split.main([str(TASKS), "--out", str(first), "--seed", "5"])
    split.main([str(TASKS), "--out", str(second), "--seed", "5"])

    assert (first / "train.json").read_text(encoding="utf-8") == (second / "train.json").read_text(encoding="utf-8")


def test_a_directory_that_holds_no_tasks_is_reported(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()

    assert split.main([str(empty), "--out", str(tmp_path / "out")]) == 2
    assert "error:" in capsys.readouterr().err


def test_a_known_benchmark_can_be_split(tmp_path: Path) -> None:
    assert split.main(["--benchmark", "long-8", "--out", str(tmp_path)]) == 0

    test = json.loads((tmp_path / "test.json").read_text(encoding="utf-8"))
    assert test["tasks"]


def test_a_task_that_names_its_domain_is_stratified_by_that_domain(tmp_path: Path) -> None:
    tasks = tmp_path / "tasks"
    tasks.mkdir()
    for index in range(4):
        (tasks / f"t{index}.yml").write_text(
            f"id: t{index}\nprompt: do it\nverify: 'true'\npath: x\ndomain: rust\n", encoding="utf-8"
        )

    assert split.main([str(tasks), "--out", str(tmp_path / "out")]) == 0

    train = json.loads((tmp_path / "out" / "train.json").read_text(encoding="utf-8"))
    test  = json.loads((tmp_path / "out" / "test.json").read_text(encoding="utf-8"))
    assert train["domains"] == ["rust"]
    assert test["domains"] == ["rust"]
    assert train["tasks"] and test["tasks"]


def test_each_side_is_written_as_the_task_files_a_run_can_read(tmp_path: Path) -> None:
    assert split.main([str(TASKS), "--out", str(tmp_path)]) == 0

    train = sorted(path.name for path in (tmp_path / "train").glob("*.yml"))
    test  = sorted(path.name for path in (tmp_path / "test").glob("*.yml"))
    assert train and test
    assert not (set(train) & set(test))


def test_the_task_files_of_a_side_keep_their_limits_and_their_path(tmp_path: Path) -> None:
    assert split.main([str(TASKS), "--out", str(tmp_path)]) == 0

    import yaml

    written = yaml.safe_load((tmp_path / "train" / sorted(path.name for path in (tmp_path / "train").glob("*.yml"))[0]).read_text(encoding="utf-8"))
    original = yaml.safe_load((TASKS / sorted(path.name for path in TASKS.glob("*.yml"))[0]).read_text(encoding="utf-8"))

    assert written["id"] == original["id"]
    assert written["verify"] == original["verify"]
    assert written["max_turns"] == original["max_turns"]
    assert Path(written["path"]).is_absolute()
    assert Path(written["path"]).is_dir()


def test_a_side_can_be_run_like_any_other_directory_of_tasks(tmp_path: Path) -> None:
    assert split.main([str(TASKS), "--out", str(tmp_path)]) == 0

    import bench_agent

    tasks = bench_agent.load_tasks(tmp_path / "train")

    assert tasks
    assert all(task.path and Path(task.path).is_dir() for task in tasks)
