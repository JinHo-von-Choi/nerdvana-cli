"""benchmarks/: every task fails on its fixture and passes once its solution overlay is applied.

Author: 최진호
Date:   2026-10-03

The shipped benchmark tasks are plain files, so they are checked without a model:
the verify command must fail on a fresh copy of the fixture (the agent is needed)
and succeed on the same copy with ``benchmarks/solutions/<id>/`` laid over it.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

BENCH     = Path(__file__).parent.parent / "benchmarks"
TASKS_DIR = BENCH / "tasks"
SOLUTIONS = BENCH / "solutions"
TASK_FILES = sorted(TASKS_DIR.glob("*.yml"))
TIMEOUT   = 60

TOOL_FOR_TAG = {"node": "node", "c": "gcc"}


def _load(file: Path) -> dict[str, Any]:
    data = yaml.safe_load(file.read_text(encoding="utf-8"))
    assert isinstance(data, dict), f"{file.name}: a task file must hold a mapping"
    return data


def _verify(command: str, workdir: Path) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    return subprocess.run(  # noqa: S602 - the command comes from the repository's own task file
        command, shell=True, cwd=workdir, capture_output=True, text=True, timeout=TIMEOUT, check=False, env=env,
    )


def _copy_fixture(task: dict[str, Any], file: Path, workdir: Path) -> None:
    fixture = (file.parent / task["path"]).resolve()
    shutil.copytree(fixture, workdir, ignore=shutil.ignore_patterns("__pycache__", ".git"))


def _require_tools(task: dict[str, Any]) -> None:
    for tag in task.get("tags", []):
        tool = TOOL_FOR_TAG.get(tag)
        if tool and shutil.which(tool) is None:
            pytest.skip(f"{tool} is not installed")


def test_there_are_tasks() -> None:
    assert len(TASK_FILES) >= 20


@pytest.mark.parametrize("file", TASK_FILES, ids=lambda f: f.stem)
def test_the_task_file_is_well_formed(file: Path) -> None:
    task = _load(file)
    assert task["id"] == file.stem
    assert task["path"] == f"../fixtures/{task['id']}"
    assert (file.parent / task["path"]).is_dir()
    tags = task.get("tags")
    assert isinstance(tags, list) and tags and all(isinstance(t, str) and t for t in tags)
    for key in ("prompt", "verify"):
        assert isinstance(task[key], str) and task[key].strip()
    for key in ("max_turns", "max_cost_usd", "timeout"):
        assert task[key] > 0


@pytest.mark.parametrize("file", TASK_FILES, ids=lambda f: f.stem)
def test_verify_fails_on_the_untouched_fixture(file: Path, tmp_path: Path) -> None:
    task    = _load(file)
    _require_tools(task)
    workdir = tmp_path / "work"
    _copy_fixture(task, file, workdir)
    result = _verify(task["verify"], workdir)
    assert result.returncode != 0, "verify already passes: the task needs no work"


@pytest.mark.parametrize("file", TASK_FILES, ids=lambda f: f.stem)
def test_verify_passes_with_the_solution_overlay(file: Path, tmp_path: Path) -> None:
    task     = _load(file)
    _require_tools(task)
    solution = SOLUTIONS / task["id"]
    assert solution.is_dir(), f"no solution overlay for {task['id']}"
    workdir  = tmp_path / "work"
    _copy_fixture(task, file, workdir)
    shutil.copytree(solution, workdir, dirs_exist_ok=True, ignore=shutil.ignore_patterns("__pycache__"))
    result = _verify(task["verify"], workdir)
    assert result.returncode == 0, f"stdout:\n{result.stdout[-1500:]}\nstderr:\n{result.stderr[-1500:]}"
