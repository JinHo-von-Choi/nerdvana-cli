#!/usr/bin/env python3
"""Split a benchmark into the tasks a library may learn from and the tasks it may not.

The question this answers is the one the domain library has to survive: does
what the library learned on one task carry over to a task of the same domain
it has never seen? That is only measurable if the two sides are kept apart
before anything runs, and if the split keeps every domain represented on both
sides -- a domain that was learned from is measured on tasks of its own.

The split is stratified by the domain a task belongs to (the language it is
about, or the name it gives itself) and it is reproducible: the same tasks
and the same seed always give the same split.

    python scripts/split_tasks.py benchmarks/tasks --out benchmarks/split
    python scripts/split_tasks.py benchmarks/tasks --test-ratio 0.4 --seed 7

The output is a directory with ``train.json`` and ``test.json``: the ids of the
tasks on each side, and the domain each of them belongs to.

Author: 최진호
Date:   2026-10-21
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
for _root in (REPO_ROOT, REPO_ROOT / "scripts"):
    if str(_root) not in sys.path:
        sys.path.insert(0, str(_root))

from nerdvana_cli.core.harness.bench_spec import (  # noqa: E402
    BenchError,
    BenchTask,
    TaskSplit,
    load_benchmark,
    split_by_domain,
)

DEFAULT_TEST_RATIO = 0.3
DEFAULT_SEED = 0


def parse_args(argv: list[str]) -> argparse.Namespace:
    """The options of the split."""
    parser = argparse.ArgumentParser(description="Split a benchmark into train and test, stratified by domain.")
    parser.add_argument("tasks", type=Path, nargs="?", help="a task file or a directory of *.yml task files")
    parser.add_argument("--benchmark", default="", help="the name of a known benchmark; the tasks path is used when it is not given")
    parser.add_argument("--source", default="", help="where the tasks of an external benchmark are")
    parser.add_argument("--out", type=Path, default=Path("benchmarks/split"), help="the directory the split is written to")
    parser.add_argument("--test-ratio", type=float, default=DEFAULT_TEST_RATIO, help=f"the share of each domain that is held back (default {DEFAULT_TEST_RATIO})")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED, help=f"the seed of the split (default {DEFAULT_SEED})")
    return parser.parse_args(argv)


def _as_bench_task(task: Any) -> BenchTask:
    """The bench task of a task the bench script read."""
    return BenchTask(
        id     = str(task.id),
        prompt = str(task.prompt),
        verify = str(task.verify),
        path   = str(task.path),
        tags   = tuple(task.tags),
        domain = str(getattr(task, "domain", "") or ""),
        file   = str(getattr(task, "file", "") or ""),
    )


def _write_task(task: BenchTask, directory: Path) -> None:
    """Write the task as a file a run can read, with its path resolved.

    The path of a task file is relative to the file itself, so a copy that
    lives somewhere else would point at nothing; it is made absolute here.
    """
    if not task.file:
        return
    source = Path(task.file)
    data   = yaml.safe_load(source.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        raw = str(data.get("path", "") or "")
        if raw and not Path(raw).is_absolute():
            data["path"] = str((source.parent / raw).resolve())
    (directory / source.name).write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


def load(tasks: Path | None, benchmark: str, source: str) -> list[BenchTask]:
    """The tasks of the benchmark: the known one, or the ones that were named."""
    if benchmark:
        return list(load_benchmark(benchmark, REPO_ROOT, source).tasks)
    from bench_agent import load_tasks

    if tasks is None:
        raise BenchError("a task file or a --benchmark is needed")

    return [_as_bench_task(task) for task in load_tasks(tasks)]


def write(split: TaskSplit, out: Path, ratio: float, seed: int) -> None:
    """Write the split, so that the run that follows knows what it may learn from.

    Each side is written twice: as the manifest of the tasks it holds, and as
    the task files themselves, so a half of the split can be run like any other
    directory of tasks.
    """
    out.mkdir(parents=True, exist_ok=True)
    for name, tasks in (("train", split.train), ("test", split.test)):
        payload = {
            "tasks":   [task.to_dict() for task in tasks],
            "ratio":   ratio,
            "seed":    seed,
            "domains": sorted({task.stratum for task in tasks}),
        }
        (out / f"{name}.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        directory = out / name
        directory.mkdir(parents=True, exist_ok=True)
        for task in tasks:
            _write_task(task, directory)


def main(argv: list[str] | None = None) -> int:
    """Split the benchmark and write the two sides down."""
    options = parse_args(sys.argv[1:] if argv is None else argv)
    if not options.tasks and not options.benchmark:
        print("error: a task file or a --benchmark is needed", file=sys.stderr)
        return 2
    try:
        tasks = load(options.tasks, options.benchmark, options.source)
        split = split_by_domain(tasks, test_ratio=options.test_ratio, seed=options.seed)
    except (BenchError, OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    write(split, options.out, options.test_ratio, options.seed)
    print(f"{len(split.train)} train task(s), {len(split.test)} test task(s) -> {options.out}")
    for domain in sorted({task.stratum for task in tasks}):
        held = [task.id for task in split.test if task.stratum == domain]
        print(f"  {domain}: {held or '(none held back)'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
