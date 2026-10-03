"""bench_compare.py: compare two benchmark runs of the same tasks.

Author: 최진호
Date:   2026-10-03

Give it the result files of two ``scripts/bench_agent.py`` runs (for instance before and after a change)::

    python scripts/bench_compare.py before.jsonl after.jsonl

For each task and overall it prints each run's pass rate, pass^k (the chance that k attempts in a row
all pass), mean input tokens and mean cost per attempt. The difference in pass rate (second run minus
first, averaged over the tasks both runs contain) comes with a 95% bootstrap interval that resamples
the tasks and, inside each task, the attempts of each run; the generator is seeded, so the same files
give the same interval. Runs whose recorded environment differs (CPU count, limits, model, sandbox,
version and the like) are flagged, because a score difference between them may come from the setup.

An interval that contains 0 means the runs cannot be told apart at this many tasks and attempts.
"""

from __future__ import annotations

import argparse
import random
import sys
from pathlib import Path
from typing import Any

from bench_agent import merge_environments, pass_hat_k
from bench_recommend import load_run

ROUNDS = 2000
SEED   = 0
ALPHA  = 0.05

Outcomes = dict[str, list[int]]


def group_by_task(attempts: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Attempts per task id, in the order the tasks first appear."""
    grouped: dict[str, list[dict[str, Any]]] = {}
    for attempt in attempts:
        grouped.setdefault(str(attempt["task_id"]), []).append(attempt)
    return grouped


def outcomes(attempts: list[dict[str, Any]]) -> Outcomes:
    """Per task, 1 for each passed attempt and 0 for each failed one."""
    return {task: [int(bool(a["passed"])) for a in rows] for task, rows in group_by_task(attempts).items()}


def mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def rate_difference(first: Outcomes, second: Outcomes) -> float:
    """Mean over the tasks both runs contain of the second run's pass rate minus the first's."""
    common = [task for task in first if task in second]
    return mean([mean(second[task]) - mean(first[task]) for task in common])


def bootstrap_difference_ci(first: Outcomes, second: Outcomes, rounds: int = ROUNDS, seed: int = SEED, alpha: float = ALPHA) -> tuple[float, float]:
    """Percentile bootstrap interval of :func:`rate_difference`; (0, 0) when the runs share no task.

    Each round draws the tasks with replacement, then draws each drawn task's attempts with replacement
    separately for the two runs, so both the choice of tasks and the luck of the attempts move the result.
    """
    common = [task for task in first if task in second]
    if not common:
        return 0.0, 0.0
    rng   = random.Random(seed)
    diffs = []
    for _ in range(rounds):
        drawn = rng.choices(common, k=len(common))
        diffs.append(mean([
            mean(rng.choices(second[task], k=len(second[task]))) - mean(rng.choices(first[task], k=len(first[task])))
            for task in drawn
        ]))
    diffs.sort()
    return diffs[int(rounds * alpha / 2)], diffs[min(int(rounds * (1 - alpha / 2)), rounds - 1)]


def environment_differences(first: dict[str, Any], second: dict[str, Any]) -> list[str]:
    """One line for each recorded fact that differs between two merged environments."""
    if not first or not second:
        return ["no environment was recorded for " + ("both runs" if not first and not second else "the first run" if not first else "the second run")]
    return [f"{key}: {first.get(key, 'unrecorded')} against {second.get(key, 'unrecorded')}" for key in sorted(set(first) | set(second)) if first.get(key) != second.get(key)]


def figures(attempts: list[dict[str, Any]], k: int) -> dict[str, Any]:
    """Pass rate, pass^k, mean input tokens and mean cost of one run's attempts."""
    passed = sum(1 for a in attempts if a["passed"])
    return {
        "attempts":           len(attempts),
        "passed":             passed,
        "pass_rate":          passed / len(attempts) if attempts else 0.0,
        "pass_hat_k":         pass_hat_k(len(attempts), passed, k),
        "mean_input_tokens":  mean([float((a.get("usage") or {}).get("input_tokens", 0)) for a in attempts]),
        "mean_cost_usd":      mean([float(a.get("cost_usd", 0.0)) for a in attempts]),
    }


def default_k(*runs: list[dict[str, Any]]) -> int:
    """The fewest attempts any task has in either run: the largest k both can answer for every task."""
    return max(1, min((len(rows) for run in runs for rows in group_by_task(run).values()), default=1))


def compare(first: list[dict[str, Any]], second: list[dict[str, Any]], k: int = 0) -> dict[str, Any]:
    """Every figure the report shows."""
    k          = k or default_k(first, second)
    first_by   = group_by_task(first)
    second_by  = group_by_task(second)
    tasks      = {task: {"first": figures(first_by.get(task, []), k), "second": figures(second_by.get(task, []), k)} for task in [*first_by, *(t for t in second_by if t not in first_by)]}
    first_out, second_out = outcomes(first), outcomes(second)
    return {
        "k":           k,
        "tasks":       tasks,
        "first":       {**figures(first, k),  "pass_hat_k": mean([tasks[t]["first"]["pass_hat_k"] for t in first_by])},
        "second":      {**figures(second, k), "pass_hat_k": mean([tasks[t]["second"]["pass_hat_k"] for t in second_by])},
        "difference":  rate_difference(first_out, second_out),
        "interval":    bootstrap_difference_ci(first_out, second_out),
        "common":      sum(1 for task in first_by if task in second_by),
        "only_first":  [task for task in first_by if task not in second_by],
        "only_second": [task for task in second_by if task not in first_by],
        "environment": environment_differences(merge_environments([a["environment"] for a in first if a.get("environment")]), merge_environments([a["environment"] for a in second if a.get("environment")])),
    }


def _cell(data: dict[str, Any]) -> str:
    if not data["attempts"]:
        return f"{'-':>7} {'-':>6} {'-':>10} {'-':>8}"
    return f"{data['passed']:>2}/{data['attempts']:<2}{data['pass_rate']:>4.0%} {data['pass_hat_k']:>6.2f} {data['mean_input_tokens']:>10,.0f} {data['mean_cost_usd']:>8.4f}"


def render(result: dict[str, Any], names: tuple[str, str]) -> str:
    """The comparison as plain text."""
    k      = result["k"]
    header = f"{'':<28} {'pass':>7} {f'pass^{k}':>6} {'in tokens':>10} {'USD':>8}   (per attempt, one block per run)"
    lines  = [f"first:  {names[0]}", f"second: {names[1]}", "", header, "-" * len(header)]
    for task, pair in result["tasks"].items():
        lines += [f"{task:<28} {_cell(pair['first'])}", f"{'':<28} {_cell(pair['second'])}"]
    lines += ["", f"{'overall, first':<28} {_cell(result['first'])}", f"{'overall, second':<28} {_cell(result['second'])}", ""]
    low, high = result["interval"]
    lines.append(f"pass rate difference, second minus first, over {result['common']} common task(s): {result['difference']:+.3f} (95% bootstrap interval {low:+.3f} to {high:+.3f})")
    lines.append("the interval contains 0: the runs cannot be told apart at this size" if low <= 0.0 <= high else "the interval excludes 0")
    first, second = result["first"], result["second"]
    if first["mean_input_tokens"]:
        lines.append(f"mean input tokens per attempt {second['mean_input_tokens'] / first['mean_input_tokens'] - 1:+.1%}")
    if first["mean_cost_usd"]:
        lines.append(f"mean cost per attempt {second['mean_cost_usd'] / first['mean_cost_usd'] - 1:+.1%}")
    for label, tasks in (("only in the first run", result["only_first"]), ("only in the second run", result["only_second"])):
        if tasks:
            lines.append(f"tasks {label} (left out of the difference): {', '.join(tasks)}")
    if result["environment"]:
        lines += ["", "WARNING: the recorded environments differ; a score difference may come from the setup:", *(f"  {line}" for line in result["environment"])]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compare two bench_agent.py result files.")
    parser.add_argument("first", type=Path, help="the baseline result file")
    parser.add_argument("second", type=Path, help="the result file to compare against it")
    parser.add_argument("--k", type=int, default=0, help="k for pass^k (default: the fewest attempts any task has)")
    options = parser.parse_args(sys.argv[1:] if argv is None else argv)
    for path in (options.first, options.second):
        if not path.is_file():
            print(f"error: {path} is not a file", file=sys.stderr)
            return 2
    runs = [load_run(options.first), load_run(options.second)]
    if not all(runs):
        print("error: a result file holds no attempts", file=sys.stderr)
        return 2
    print(render(compare(runs[0], runs[1], options.k), (options.first.name, options.second.name)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
