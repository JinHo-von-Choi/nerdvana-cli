"""bench_agent.py: measure how often the agent solves fixed repository tasks.

Author: 최진호
Date:   2026-10-03

Each task names a repository (a local directory or a git URL with a ref), a prompt
and a verify command. For every attempt the script copies the repository into a fresh
working directory, runs ``nerdvana run`` there with a turn and cost ceiling, then runs
the verify command. The verify command's exit status is the only judgement: output
text is never compared. Results are appended to a JSONL file and summarised as
pass rate, pass@k, cost and time per task.

This script calls real model APIs and bills for them. It is a manual tool and runs
outside pytest and CI. Run it in a disposable environment: the agent executes shell
commands with ``--approval-mode yolo`` by default. ``--sandbox require`` (the default) limits
what they can write to the working directory and the temporary directories on Linux. It does
not stop reading, running programs or UDP, so a disposable environment is still advised.

Usage::

    python scripts/bench_agent.py benchmarks/tasks --attempts 3 --yes \\
        [--model M] [--provider P] [--approval-mode yolo] [--sandbox require] [--out results.jsonl]

Without ``--yes`` the script only prints the tasks and the worst-case spend.

Exit codes: 0 finished (whatever the pass rate), 2 unusable task files or options.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import shutil
import subprocess
import sys
import tempfile
import time
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml

DEFAULT_MAX_TURNS = 30
DEFAULT_MAX_COST  = 1.0
DEFAULT_TIMEOUT   = 1200
SETUP_TIMEOUT     = 600
VERIFY_TIMEOUT    = 600


class TaskError(ValueError):
    """A task file is unusable."""


@dataclass(frozen=True)
class Task:
    """One repository task."""

    id:           str
    prompt:       str
    verify:       str
    path:         str   = ""
    repo:         str   = ""
    ref:          str   = ""
    setup:        str   = ""
    max_turns:    int   = DEFAULT_MAX_TURNS
    max_cost_usd: float = DEFAULT_MAX_COST
    timeout:      int   = DEFAULT_TIMEOUT
    tags:         tuple[str, ...] = ()


@dataclass
class Attempt:
    """What one attempt produced."""

    task_id:      str
    attempt:      int
    passed:       bool
    stop:         str   = ""
    exit_code:    int   = 0
    turns:        int   = 0
    cost_usd:     float = 0.0
    duration_s:   float = 0.0
    error:        str   = ""
    signals:      dict[str, int] = field(default_factory=dict)
    usage:        dict[str, int] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Task files
# ---------------------------------------------------------------------------


def parse_task(data: Any, base: Path) -> Task:
    """Build a Task from one parsed YAML mapping; relative paths resolve against *base*."""
    if not isinstance(data, dict):
        raise TaskError("a task file must hold a mapping")
    for key in ("id", "prompt", "verify"):
        if not isinstance(data.get(key), str) or not data[key].strip():
            raise TaskError(f"'{key}' is required")
    path, repo = str(data.get("path", "") or ""), str(data.get("repo", "") or "")
    if bool(path) == bool(repo):
        raise TaskError("name exactly one of 'path' and 'repo'")
    if path and not Path(path).is_absolute():
        path = str((base / path).resolve())
    return Task(
        id           = data["id"].strip(),
        prompt       = data["prompt"],
        verify       = data["verify"],
        path         = path,
        repo         = repo,
        ref          = str(data.get("ref", "") or ""),
        setup        = str(data.get("setup", "") or ""),
        max_turns    = int(data.get("max_turns", DEFAULT_MAX_TURNS)),
        max_cost_usd = float(data.get("max_cost_usd", DEFAULT_MAX_COST)),
        timeout      = int(data.get("timeout", DEFAULT_TIMEOUT)),
        tags         = tuple(str(tag) for tag in data.get("tags") or ()),
    )


def load_tasks(source: Path) -> list[Task]:
    """Tasks from one YAML file or every ``*.yml`` in a directory, in file name order."""
    files = [source] if source.is_file() else sorted(source.glob("*.yml"))
    if not files:
        raise TaskError(f"no task files in {source}")
    tasks: list[Task] = []
    for file in files:
        try:
            tasks.append(parse_task(yaml.safe_load(file.read_text(encoding="utf-8")), file.parent))
        except (TaskError, yaml.YAMLError, ValueError, OSError) as exc:
            raise TaskError(f"{file}: {exc}") from exc
    ids = [t.id for t in tasks]
    if len(set(ids)) != len(ids):
        raise TaskError("task ids must be unique")
    return tasks


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------


def pass_at_k(attempts: int, passed: int, k: int) -> float:
    """Chance that at least one of k attempts drawn from the recorded ones passes.

    Unbiased estimator ``1 - C(n - c, k) / C(n, k)`` for n attempts with c passes.
    """
    if attempts <= 0 or k <= 0:
        return 0.0
    k = min(k, attempts)
    if attempts - passed < k:
        return 1.0
    return 1.0 - math.comb(attempts - passed, k) / math.comb(attempts, k)


def bootstrap_ci(values: list[float], rounds: int = 2000, seed: int = 0, alpha: float = 0.05) -> tuple[float, float]:
    """Percentile bootstrap interval of the mean of *values* (here: per-task pass rates).

    Resampling tasks with replacement shows how much the mean could move had other tasks of the
    same kind been drawn. The generator is seeded, so the same input gives the same interval.
    """
    if not values:
        return 0.0, 0.0
    rng   = random.Random(seed)
    means = sorted(sum(rng.choices(values, k=len(values))) / len(values) for _ in range(rounds))
    return means[int(rounds * alpha / 2)], means[min(int(rounds * (1 - alpha / 2)), rounds - 1)]


def summarize(attempts: list[Attempt], k: int, tags: dict[str, tuple[str, ...]] | None = None) -> dict[str, Any]:
    """Per-task, per-tag and overall figures for the recorded attempts."""
    by_task: dict[str, list[Attempt]] = {}
    for attempt in attempts:
        by_task.setdefault(attempt.task_id, []).append(attempt)
    tasks = []
    for task_id, group in by_task.items():
        passed = sum(1 for a in group if a.passed)
        tasks.append({
            "task":         task_id,
            "attempts":     len(group),
            "passed":       passed,
            "pass_at_1":    pass_at_k(len(group), passed, 1),
            f"pass_at_{k}": pass_at_k(len(group), passed, k),
            "cost_usd":     sum(a.cost_usd for a in group),
            "mean_seconds": sum(a.duration_s for a in group) / len(group),
        })
    total_cost = sum(a.cost_usd for a in attempts)
    solved     = sum(1 for t in tasks if t["passed"])
    by_tag: dict[str, list[float]] = {}
    for task in tasks:
        for tag in (tags or {}).get(task["task"], ()):
            by_tag.setdefault(tag, []).append(task["pass_at_1"])
    low, high = bootstrap_ci([t["pass_at_1"] for t in tasks])
    failed_signals: dict[str, int] = {}
    passed_signals: dict[str, int] = {}
    for attempt in attempts:
        bucket = passed_signals if attempt.passed else failed_signals
        for name, count in attempt.signals.items():
            bucket[name] = bucket.get(name, 0) + count
    return {
        "signals_in_failed_attempts": dict(sorted(failed_signals.items())),
        "signals_in_passed_attempts": dict(sorted(passed_signals.items())),
        "stop_reasons":               dict(sorted(Counter(a.stop or "none" for a in attempts if not a.passed).items())),
        "tokens":                     {key: sum(a.usage.get(key, 0) for a in attempts) for key in ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens")},
        "mean_pass_at_1_ci":   [low, high],
        "by_tag":              {tag: {"tasks": len(rates), "mean_pass_at_1": sum(rates) / len(rates)} for tag, rates in sorted(by_tag.items())},
        "k":                   k,
        "tasks":               tasks,
        "attempts":            len(attempts),
        "passed_attempts":     sum(1 for a in attempts if a.passed),
        "tasks_solved":        solved,
        "mean_pass_at_1":      sum(t["pass_at_1"] for t in tasks) / len(tasks) if tasks else 0.0,
        "total_cost_usd":      total_cost,
        "cost_per_solved_task": total_cost / solved if solved else None,
    }


def render(summary: dict[str, Any]) -> str:
    """The summary as a plain text table."""
    k      = summary["k"]
    header = f"{'task':<28} {'n':>3} {'pass':>4} {'pass@1':>7} {f'pass@{k}':>7} {'cost USD':>9} {'mean s':>7}"
    lines  = [header, "-" * len(header)]
    for t in summary["tasks"]:
        lines.append(
            f"{t['task']:<28} {t['attempts']:>3} {t['passed']:>4} {t['pass_at_1']:>7.2f} "
            f"{t[f'pass_at_{k}']:>7.2f} {t['cost_usd']:>9.4f} {t['mean_seconds']:>7.1f}"
        )
    if summary["by_tag"]:
        lines += ["", "by tag"]
        lines += [f"  {tag:<20} {data['tasks']:>3} task(s)  mean pass@1 {data['mean_pass_at_1']:.2f}" for tag, data in summary["by_tag"].items()]
    if summary["stop_reasons"]:
        lines += ["", "how failed attempts ended"]
        lines += [f"  {reason:<24} {count:>3}" for reason, count in summary["stop_reasons"].items()]
    if summary["signals_in_failed_attempts"] or summary["signals_in_passed_attempts"]:
        names = sorted(set(summary["signals_in_failed_attempts"]) | set(summary["signals_in_passed_attempts"]))
        lines += ["", f"{'signal (count over attempts)':<30} {'in failed':>9} {'in passed':>9}"]
        lines += [f"  {name:<28} {summary['signals_in_failed_attempts'].get(name, 0):>9} {summary['signals_in_passed_attempts'].get(name, 0):>9}" for name in names]
    tokens = summary["tokens"]
    if tokens["input_tokens"]:
        lines += ["", f"tokens: {tokens['input_tokens']:,} in ({tokens['cache_read_tokens']:,} from cache), {tokens['output_tokens']:,} out"]
    per_solved = summary["cost_per_solved_task"]
    lines.append("")
    lines.append(
        f"tasks solved {summary['tasks_solved']}/{len(summary['tasks'])}, mean pass@1 {summary['mean_pass_at_1']:.2f} "
        f"(95% bootstrap interval {summary['mean_pass_at_1_ci'][0]:.2f} to {summary['mean_pass_at_1_ci'][1]:.2f}), "
        f"total cost ${summary['total_cost_usd']:.4f}, "
        f"cost per solved task {'n/a' if per_solved is None else f'${per_solved:.4f}'}"
    )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Running
# ---------------------------------------------------------------------------


def _shell(command: str, cwd: Path, timeout: int) -> subprocess.CompletedProcess[str] | None:
    """Run a shell command; None when it timed out."""
    try:
        return subprocess.run(  # noqa: S602 - the command comes from the operator's own task file
            command, shell=True, cwd=cwd, capture_output=True, text=True, timeout=timeout, check=False,
        )
    except subprocess.TimeoutExpired:
        return None


def prepare(task: Task, workdir: Path) -> str:
    """Fill *workdir* with the task's repository; returns an error text, empty on success."""
    try:
        if task.path:
            shutil.copytree(task.path, workdir, dirs_exist_ok=True, ignore=shutil.ignore_patterns(".git", "__pycache__"))
        else:
            clone = subprocess.run(["git", "clone", "--quiet", task.repo, str(workdir)], capture_output=True, text=True, check=False)
            if clone.returncode:
                return f"clone failed: {clone.stderr.strip()[:300]}"
            if task.ref:
                checkout = subprocess.run(["git", "-C", str(workdir), "checkout", "--quiet", task.ref], capture_output=True, text=True, check=False)
                if checkout.returncode:
                    return f"checkout failed: {checkout.stderr.strip()[:300]}"
    except OSError as exc:
        return f"could not prepare the repository: {exc}"
    if task.setup:
        done = _shell(task.setup, workdir, SETUP_TIMEOUT)
        if done is None or done.returncode:
            return "setup command failed or timed out"
    return ""


def agent_command(task: Task, options: argparse.Namespace) -> list[str]:
    """The ``nerdvana run`` invocation for a task."""
    command = [
        sys.executable, "-c", "from nerdvana_cli.main import app; app()", "run", task.prompt,
        "--output-format", "json",
        "--max-turns", str(task.max_turns),
        "--max-cost-usd", str(task.max_cost_usd),
        "--approval-mode", options.approval_mode,
        "--sandbox", options.sandbox,
    ]
    if options.model:
        command += ["--model", options.model]
    if options.provider:
        command += ["--provider", options.provider]
    if options.gate:
        command += ["--verify", task.verify]
    for assignment in options.set:
        command += ["--set", assignment]
    return command


def parse_result(stdout: str) -> dict[str, Any]:
    """The last JSON ``result`` object in the agent's standard output, empty when there is none."""
    for line in reversed(stdout.strip().splitlines()):
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict) and data.get("type") == "result":
            return data
    return {}


def run_attempt(task: Task, number: int, options: argparse.Namespace, root: Path) -> Attempt:
    """One attempt in a fresh working directory."""
    workdir = root / f"{task.id}-{number}"
    result  = Attempt(task_id=task.id, attempt=number, passed=False)
    problem = prepare(task, workdir)
    if problem:
        result.error = problem
        return result

    started = time.monotonic()
    try:
        agent = subprocess.run(
            agent_command(task, options), cwd=workdir, capture_output=True, text=True, timeout=task.timeout, check=False,
        )
        report           = parse_result(agent.stdout)
        result.exit_code = agent.returncode
        result.stop      = str(report.get("subtype", ""))
        result.turns     = int(report.get("num_turns", 0) or 0)
        result.cost_usd  = float(report.get("total_cost_usd", 0.0) or 0.0)
        result.signals   = {str(k): int(v) for k, v in (report.get("signals") or {}).items()}
        result.usage     = {str(k): int(v) for k, v in (report.get("usage") or {}).items()}
        if not report:
            result.error = f"no result object in the output (exit {agent.returncode}): {agent.stderr.strip()[-300:]}"
        elif report.get("is_error"):
            result.error = str(report.get("error") or report.get("result") or "")[-300:]
    except subprocess.TimeoutExpired:
        result.stop  = "timeout"
        result.error = f"agent exceeded {task.timeout}s"
    result.duration_s = time.monotonic() - started

    verdict       = _shell(task.verify, workdir, VERIFY_TIMEOUT)
    result.passed = verdict is not None and verdict.returncode == 0
    return result


def worst_case_cost(tasks: list[Task], attempts: int) -> float:
    """Most the run can spend if every attempt reaches its cost ceiling."""
    return sum(t.max_cost_usd for t in tasks) * attempts


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Measure the agent's success rate on fixed repository tasks.")
    parser.add_argument("tasks", type=Path, help="a task file or a directory of *.yml task files")
    parser.add_argument("--set", action="append", default=[], metavar="SECTION.FIELD=VALUE", help="override a setting in every attempt (nerdvana run --set), e.g. to compare compaction thresholds")
    parser.add_argument("--gate", action="store_true", help="hold the agent to each task's verify command (nerdvana run --verify) instead of checking only afterwards")
    parser.add_argument("--tag", action="append", default=[], help="run only tasks carrying this tag (repeatable)")
    parser.add_argument("--attempts", type=int, default=1, help="attempts per task (default 1)")
    parser.add_argument("--k", type=int, default=0, help="k for pass@k (default: the number of attempts)")
    parser.add_argument("--model", default="")
    parser.add_argument("--provider", default="")
    parser.add_argument("--approval-mode", default="yolo", choices=["default", "auto_edit", "yolo", "plan"])
    parser.add_argument("--sandbox", default="require", choices=["off", "auto", "require"],
                        help="confine each attempt's shell commands to its working directory (default require; off on systems without Landlock)")
    parser.add_argument("--out", type=Path, default=Path("bench-results.jsonl"), help="JSONL file the attempts are appended to")
    parser.add_argument("--keep-workdirs", action="store_true", help="keep each attempt's working directory for inspection")
    parser.add_argument("--yes", action="store_true", help="spend money: run the agent (without it only the plan is printed)")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    options = parse_args(sys.argv[1:] if argv is None else argv)
    if options.attempts < 1:
        print("--attempts must be at least 1", file=sys.stderr)
        return 2
    try:
        tasks = load_tasks(options.tasks)
    except TaskError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if options.tag:
        tasks = [t for t in tasks if set(options.tag) & set(t.tags)]
        if not tasks:
            print(f"error: no task carries any of the tags {options.tag}", file=sys.stderr)
            return 2

    print(f"{len(tasks)} task(s) x {options.attempts} attempt(s); worst-case spend ${worst_case_cost(tasks, options.attempts):.2f}")
    if not options.yes:
        print("dry run: pass --yes to run the agent against the real API")
        return 0

    k        = options.k or options.attempts
    attempts: list[Attempt] = []
    root     = Path(tempfile.mkdtemp(prefix="nerdvana-bench-"))
    try:
        for task in tasks:
            for number in range(1, options.attempts + 1):
                attempt = run_attempt(task, number, options, root)
                attempts.append(attempt)
                with options.out.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(asdict(attempt)) + "\n")
                print(f"{task.id} #{number}: {'pass' if attempt.passed else 'fail'} ({attempt.stop or 'no result'}, ${attempt.cost_usd:.4f}, {attempt.duration_s:.0f}s)", flush=True)
    finally:
        if options.keep_workdirs:
            print(f"working directories kept in {root}")
        else:
            shutil.rmtree(root, ignore_errors=True)
    print()
    print(render(summarize(attempts, k, {t.id: t.tags for t in tasks})))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
