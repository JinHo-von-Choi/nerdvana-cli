"""bench_recommend.py: suggest which model each kind of task should run on, from benchmark results.

Author: 최진호
Date:   2026-10-03

Run ``scripts/bench_agent.py`` once per model (or per routing policy) with the same tasks and ``--out``
files, then give each result file a label that is the model spec::

    python scripts/bench_recommend.py benchmarks/tasks \\
        claude-haiku-4-5-20251001=haiku.jsonl claude-sonnet-5-5=sonnet.jsonl openai:gpt-4.1=gpt.jsonl

For each tag the tasks carry, the script compares the runs on pass rate and cost and names the cheapest run
whose pass rate cannot be told apart from the best one. A suggestion needs enough attempts: with few
attempts the intervals are wide, nothing can be told apart, and the answer is "not enough data" rather
than a guess. The output includes an ``agents.categories`` block to paste into the configuration; nothing is
written for you.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

MIN_ATTEMPTS = 8
Z            = 1.96


@dataclass(frozen=True)
class Cell:
    """One run's results on the tasks of one tag."""

    label:    str
    attempts: int
    passed:   int
    cost:     float

    @property
    def rate(self) -> float:
        return self.passed / self.attempts if self.attempts else 0.0

    @property
    def cost_per_attempt(self) -> float:
        return self.cost / self.attempts if self.attempts else 0.0


def wilson(passed: int, attempts: int, z: float = Z) -> tuple[float, float]:
    """Wilson score interval of a pass rate; (0, 1) when there are no attempts."""
    if attempts <= 0:
        return 0.0, 1.0
    p      = passed / attempts
    denom  = 1 + z * z / attempts
    centre = (p + z * z / (2 * attempts)) / denom
    half   = z * math.sqrt(p * (1 - p) / attempts + z * z / (4 * attempts * attempts)) / denom
    return max(centre - half, 0.0), min(centre + half, 1.0)


def load_tags(tasks: Path) -> dict[str, list[str]]:
    """Task id to its tags, from the task files."""
    files = [tasks] if tasks.is_file() else sorted(tasks.glob("*.yml"))
    tags: dict[str, list[str]] = {}
    for file in files:
        data = yaml.safe_load(file.read_text(encoding="utf-8")) or {}
        tags[str(data.get("id", file.stem))] = [str(t) for t in data.get("tags") or []]
    return tags


def load_run(path: Path) -> list[dict[str, Any]]:
    """The attempts of one run (a JSONL file written by bench_agent.py)."""
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def cells_by_tag(runs: dict[str, list[dict[str, Any]]], tags: dict[str, list[str]]) -> dict[str, list[Cell]]:
    """For each tag, one Cell per run that has attempts on tasks carrying it."""
    table: dict[str, list[Cell]] = {}
    for label, attempts in runs.items():
        grouped: dict[str, list[dict[str, Any]]] = {}
        for attempt in attempts:
            for tag in tags.get(attempt["task_id"], ()):
                grouped.setdefault(tag, []).append(attempt)
        for tag, rows in grouped.items():
            table.setdefault(tag, []).append(Cell(label, len(rows), sum(1 for r in rows if r["passed"]), sum(float(r.get("cost_usd", 0.0)) for r in rows)))
    return table


def recommend(cells: list[Cell], min_attempts: int = MIN_ATTEMPTS) -> tuple[Cell | None, str]:
    """The cheapest run whose pass rate overlaps the best run's, or None with the reason.

    Two rates overlap when each one's Wilson interval reaches the other's point estimate range
    (the intervals intersect); only runs with *min_attempts* attempts or more are compared.
    """
    usable = [c for c in cells if c.attempts >= min_attempts]
    if len(usable) < 2:
        return None, f"not enough data (needs at least two runs with {min_attempts} attempts on this tag)"
    best          = max(usable, key=lambda c: (c.rate, -c.cost_per_attempt))
    best_low, _   = wilson(best.passed, best.attempts)
    adequate      = [c for c in usable if wilson(c.passed, c.attempts)[1] >= best_low]
    choice        = min(adequate, key=lambda c: (c.cost_per_attempt, -c.rate))
    if choice.label == best.label:
        return choice, "the best pass rate is also the cheapest of the runs that match it"
    return choice, f"matches the best pass rate within the interval at {choice.cost_per_attempt:.4f} against {best.cost_per_attempt:.4f} USD per attempt"


def pareto(cells: list[Cell]) -> list[Cell]:
    """Runs no other run beats on both pass rate and cost."""
    return [c for c in cells if not any(o is not c and o.rate >= c.rate and o.cost_per_attempt <= c.cost_per_attempt and (o.rate > c.rate or o.cost_per_attempt < c.cost_per_attempt) for o in cells)]


def render(table: dict[str, list[Cell]], min_attempts: int = MIN_ATTEMPTS) -> str:
    """The comparison, the choices and a configuration block."""
    lines: list[str] = []
    mapping: dict[str, str] = {}
    for tag in sorted(table):
        cells = sorted(table[tag], key=lambda c: c.label)
        front = {c.label for c in pareto(cells)}
        lines.append(f"{tag}")
        for cell in cells:
            low, high = wilson(cell.passed, cell.attempts)
            lines.append(f"  {cell.label:<34} {cell.passed:>3}/{cell.attempts:<3} {cell.rate:>5.2f} [{low:.2f}, {high:.2f}]  {cell.cost_per_attempt:>8.4f} USD/attempt{'  pareto' if cell.label in front else ''}")
        choice, why = recommend(cells, min_attempts)
        lines.append(f"  -> {choice.label if choice else 'no suggestion'}: {why}")
        lines.append("")
        if choice is not None:
            mapping[tag] = choice.label
    if mapping:
        lines += ["agents:", "  categories:", *(f"    {tag}: {label}" for tag, label in mapping.items())]
    else:
        lines.append("No tag has enough data for a suggestion.")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Suggest a model per task tag from benchmark results.")
    parser.add_argument("tasks", type=Path, help="the task file or directory the runs used (for the tags)")
    parser.add_argument("runs", nargs="+", help="LABEL=results.jsonl, the label being the model spec")
    parser.add_argument("--min-attempts", type=int, default=MIN_ATTEMPTS)
    options = parser.parse_args(sys.argv[1:] if argv is None else argv)
    runs: dict[str, list[dict[str, Any]]] = {}
    for item in options.runs:
        label, sep, path = item.partition("=")
        if not sep or not label or not Path(path).is_file():
            print(f"error: '{item}' is not LABEL=existing-file", file=sys.stderr)
            return 2
        runs[label] = load_run(Path(path))
    print(render(cells_by_tag(runs, load_tags(options.tasks)), options.min_attempts))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
