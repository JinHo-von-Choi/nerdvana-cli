"""Generate the long-horizon benchmark fixtures, solutions and task files.

Author: 최진호
Date:   2026-10-03

The fixtures are too large to write by hand, so they are produced here from fixed seeds.
Running the script rewrites ``tasks/``, ``fixtures/`` and ``solutions/`` next to it;
``--check`` only reports whether the committed files equal a fresh generation, and
``--out DIR`` generates into another directory.

    python benchmarks/long/generate.py [--check] [--out DIR] [--sizes]
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from gen import (  # noqa: E402
    bughunt,
    config_migration,
    data_repair,
    docs_table,
    layers,
    log_analysis,
    move_function,
    rename,
)
from gen.common import MAX_COST_USD, MAX_TURNS, TIMEOUT, TaskBuild  # noqa: E402

MODULES = (rename, data_repair, log_analysis, move_function, docs_table, bughunt, layers, config_migration)


def build_all() -> list[TaskBuild]:
    """Every task, in a fixed order."""
    return [module.build() for module in MODULES]


def task_yaml(build: TaskBuild) -> str:
    """The task file text: the generator's mapping plus the fields every long task shares."""
    task = {
        "id": build.task_id,
        "path": f"../fixtures/{build.task_id}",
        **build.task,
        "max_turns": MAX_TURNS,
        "max_cost_usd": MAX_COST_USD,
        "timeout": TIMEOUT,
    }
    task["tags"] = ["long", *build.task["tags"]]
    return yaml.safe_dump(task, sort_keys=False, width=100000, allow_unicode=True)


def expected_files(build: TaskBuild) -> dict[str, str]:
    """Every file of a task keyed by its path below the output root."""
    files = {f"tasks/{build.task_id}.yml": task_yaml(build)}
    files.update({f"fixtures/{build.task_id}/{path}": text for path, text in build.fixture.items()})
    files.update({f"solutions/{build.task_id}/{path}": text for path, text in build.solution.items()})
    return files


def write_all(root: Path, builds: list[TaskBuild]) -> None:
    """Replace the generated directories under *root*."""
    for name in ("tasks", "fixtures", "solutions"):
        shutil.rmtree(root / name, ignore_errors=True)
    for build in builds:
        for path, text in expected_files(build).items():
            target = root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8", newline="\n")


def differences(root: Path, builds: list[TaskBuild]) -> list[str]:
    """Paths whose committed content differs from a fresh generation, plus stray files."""
    expected = {path for build in builds for path in expected_files(build)}
    found    = {p.relative_to(root).as_posix() for name in ("tasks", "fixtures", "solutions")
                for p in (root / name).rglob("*") if p.is_file() and "__pycache__" not in p.parts}
    problems = [f"stray {p}" for p in sorted(found - expected)]
    for build in builds:
        for path, text in sorted(expected_files(build).items()):
            target = root / path
            if not target.is_file() or target.read_text(encoding="utf-8") != text:
                problems.append(f"differs {path}")
    return problems


def print_sizes(builds: list[TaskBuild]) -> None:
    """Fixture size and the characters of the files an agent is expected to read."""
    for build in builds:
        total   = sum(len(text) for text in build.fixture.values())
        reading = sum(len(build.fixture[path]) for path in build.reading)
        print(f"{build.task_id}: fixture {total} chars in {len(build.fixture)} files; expected reading {reading} chars (~{reading // 4} tokens)")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="compare the committed files with a fresh generation")
    parser.add_argument("--out", type=Path, default=HERE, help="output root (default: this directory)")
    parser.add_argument("--sizes", action="store_true", help="print the size of each fixture and its expected reading")
    options = parser.parse_args(argv)
    builds  = build_all()
    if options.sizes:
        print_sizes(builds)
    if options.check:
        problems = differences(options.out, builds)
        print("\n".join(problems) if problems else "generated files are up to date")
        return 1 if problems else 0
    write_all(options.out, builds)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
