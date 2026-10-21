"""bench_agent.py: measure how often the agent solves fixed repository tasks.

Author: 최진호
Date:   2026-10-03

Each task names a repository (a local directory or a git URL with a ref), a prompt
and a verify command. For every attempt the script copies the repository into a fresh
working directory, runs ``nerdvana run`` there with a turn and cost ceiling, then runs
the verify command. The verify command's exit status is the only judgement: output
text is never compared. Results are appended to a JSONL file and summarised as
pass rate, pass@k, pass^k, cost and time per task.

Every result line also records the facts that move scores (CPU count, memory, limits, model,
sandbox mode, nerdvana version, git commit) and an audit of the agent's tool calls for
solution-lookup behaviour. ``--isolate`` and ``--no-network`` narrow what the agent can look up.
``scripts/bench_compare.py`` compares two result files.

This script calls real model APIs and bills for them. It is a manual tool and runs
outside pytest and CI. Run it in a disposable environment: the agent executes shell
commands with ``--approval-mode yolo`` by default. ``--sandbox require`` (the default) limits
what they can write to the working directory and the temporary directories on Linux. It does
not stop reading, running programs or UDP, so a disposable environment is still advised.

Usage::

    python scripts/bench_agent.py benchmarks/tasks --attempts 4 --yes \\
        [--model M] [--provider P] [--approval-mode yolo] [--sandbox require] \\
        [--isolate] [--no-network] [--out results.jsonl]

Use 4 or more attempts per task: pass^k and the intervals mean little from fewer. Without
``--yes`` the script only prints the tasks and the worst-case spend.

Exit codes: 0 finished (whatever the pass rate), 2 unusable task files or options.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import time
from collections import Counter
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any

import yaml

from nerdvana_cli import __version__
from nerdvana_cli.core.config import paths as core_paths
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
    write_runmark,
)
from nerdvana_cli.core.harness.library import DomainLibrary, ExperienceNote
from nerdvana_cli.core.harness.ppc import AnonymizedSelector, AnonymousCandidate, Candidate
from nerdvana_cli.core.harness.surface import NERDVANA_SURFACE
from nerdvana_cli.core.harness.transfer import (
    changed_tree,
    front_matter_fields,
    give_menu,
    give_tree,
    read_tree,
    tree_of,
)
from nerdvana_cli.core.safety.sandbox import landlock_abi

DEFAULT_MAX_TURNS    = 30
DEFAULT_MAX_COST     = 1.0
DEFAULT_TIMEOUT      = 1200
SETUP_TIMEOUT        = 600
VERIFY_TIMEOUT       = 600
RECOMMENDED_ATTEMPTS = 4
MIN_USEFUL_ATTEMPTS  = 3
NO_NETWORK_ABI       = 4

# What the task is told when the run may leave behind what it learned. The
# lesson is written for the next task of the domain, so it does not name this
# one: a lesson that names a task is not one the next task can use.
LEARN_INSTRUCTION = """

When the task is done, leave behind what made it work, so the next task of this kind starts from it.
Write a skill at .nerdvana/skills/<name>/SKILL.md (front matter with a name and a description) that
states the approach in general terms, and a helper script under scripts/ when a script is what made it
work. Keep it short, and do not name this task: write it for the next one.
"""

REPO_ROOT            = Path(__file__).resolve().parent.parent
GIT_IDENTITY         = ("-c", "user.name=bench", "-c", "user.email=bench@localhost", "-c", "commit.gpgsign=false")

# Package registries a download from is not counted as looking up an answer.
REGISTRY_HOSTS = frozenset({
    "pypi.org", "files.pythonhosted.org", "registry.npmjs.org", "registry.yarnpkg.com",
    "crates.io", "static.crates.io", "proxy.golang.org",
})
_GIT_ROOT_OPTIONS = r"(?:(?:-C|-c|--git-dir|--work-tree)\s+\S+\s+|--\S+\s+)*"
_GIT_HISTORY      = re.compile(rf"\bgit\s+{_GIT_ROOT_OPTIONS}(?:log|show|reflog|rev-list|cat-file|blame|fetch|pull|ls-remote|clone|show-ref|for-each-ref)\b")
_GIT_INTERNALS    = re.compile(r"\.git[\\/]+(?:objects|packed-refs|refs|logs|ORIG_HEAD|FETCH_HEAD)")
_PACKAGE_FETCH    = re.compile(r"\b(?:pip3?|python3?\s+-m\s+pip|uv\s+pip|pipx)\s+(?:install|download)\b|\buv\s+(?:add|tool\s+install)\b|\b(?:npm|pnpm|yarn)\s+(?:install|i|add|pack|view|info)\b")
_URL_FETCH        = re.compile(r"\b(?:curl|wget)\b")
_URL_HOST         = re.compile(r"https?://([^/\s\"'\\:]+)")
_WEB_TOOLS        = frozenset({"WebFetch", "WebSearch"})


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
    domain:       str   = ""
    # The file the task was read from, when it was read from one.
    file:         str   = ""


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
    environment:  dict[str, Any] = field(default_factory=dict)
    audit:        list[dict[str, str]] = field(default_factory=list)
    candidates:   int = 1
    # What the run changed in the harness: the files it left behind. It is not
    # written to the result file; the paths it holds are.
    harness:      dict[str, str] = field(default_factory=dict)

    def to_record(self) -> dict[str, Any]:
        """The attempt as it is written to the result file."""
        record = asdict(self)
        record.pop("harness", None)
        record["harness_paths"] = sorted(self.harness)
        return record


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
        domain       = str(data.get("domain", "") or ""),
    )


def load_tasks(source: Path) -> list[Task]:
    """Tasks from one YAML file or every ``*.yml`` in a directory, in file name order."""
    files = [source] if source.is_file() else sorted(source.glob("*.yml"))
    if not files:
        raise TaskError(f"no task files in {source}")
    tasks: list[Task] = []
    for file in files:
        try:
            task = parse_task(yaml.safe_load(file.read_text(encoding="utf-8")), file.parent)
        except (TaskError, yaml.YAMLError, ValueError, OSError) as exc:
            raise TaskError(f"{file}: {exc}") from exc
        tasks.append(replace(task, file=str(file)))
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


def pass_hat_k(attempts: int, passed: int, k: int) -> float:
    """Chance that all k attempts drawn from the recorded ones pass (pass^k, the reliability of a task).

    Unbiased estimator ``C(c, k) / C(n, k)`` for n attempts with c passes. A k above n is cut to n,
    as in :func:`pass_at_k`: the answer is then 1 when every recorded attempt passed and 0 otherwise.
    """
    if attempts <= 0 or k <= 0:
        return 0.0
    k = min(k, attempts)
    return math.comb(passed, k) / math.comb(attempts, k)


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


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _task_rows(attempts: list[Attempt], k: int) -> list[dict[str, Any]]:
    """One row of figures per task, in the order the tasks first appear."""
    by_task: dict[str, list[Attempt]] = {}
    for attempt in attempts:
        by_task.setdefault(attempt.task_id, []).append(attempt)
    rows = []
    for task_id, group in by_task.items():
        passed = sum(1 for a in group if a.passed)
        rows.append({
            "task":           task_id,
            "attempts":       len(group),
            "passed":         passed,
            "pass_at_1":      pass_at_k(len(group), passed, 1),
            f"pass_at_{k}":   pass_at_k(len(group), passed, k),
            f"pass_hat_{k}":  pass_hat_k(len(group), passed, k),
            "cost_usd":       sum(a.cost_usd for a in group),
            "mean_seconds":   sum(a.duration_s for a in group) / len(group),
        })
    return rows


def _signal_totals(attempts: list[Attempt], passed: bool) -> dict[str, int]:
    """How often each kind of trouble was counted across the passed (or the failed) attempts."""
    totals: dict[str, int] = {}
    for attempt in attempts:
        if attempt.passed == passed:
            for name, count in attempt.signals.items():
                totals[name] = totals.get(name, 0) + count
    return dict(sorted(totals.items()))


def _audit_figures(attempts: list[Attempt]) -> dict[str, Any]:
    """How many attempts the audit flagged, how many of them passed, and for which kinds of lookup."""
    flagged = [a for a in attempts if a.audit]
    kinds   = Counter(kind for a in flagged for kind in {entry["kind"] for entry in a.audit})
    return {
        "audit_flagged_attempts": len(flagged),
        "audit_flagged_passed":   sum(1 for a in flagged if a.passed),
        "audit_kinds":            dict(sorted(kinds.items())),
    }


def merge_environments(environments: list[dict[str, Any]]) -> dict[str, Any]:
    """One record from many: a key keeps its value when every attempt agrees, else the sorted distinct values."""
    merged: dict[str, Any] = {}
    for key in sorted({key for env in environments for key in env}):
        distinct = {json.dumps(env[key], sort_keys=True): env[key] for env in environments if key in env}
        merged[key] = next(iter(distinct.values())) if len(distinct) == 1 else [distinct[name] for name in sorted(distinct)]
    return merged


def summary_warnings(tasks: list[dict[str, Any]]) -> list[str]:
    """Cautions the figures call for."""
    fewest = min((t["attempts"] for t in tasks), default=RECOMMENDED_ATTEMPTS)
    if fewest < MIN_USEFUL_ATTEMPTS:
        return [f"{fewest} attempt(s) on the thinnest task: pass@k, pass^k and the intervals say little; use {RECOMMENDED_ATTEMPTS} or more attempts per task"]
    return []


def summarize(attempts: list[Attempt], k: int, tags: dict[str, tuple[str, ...]] | None = None) -> dict[str, Any]:
    """Per-task, per-tag and overall figures for the recorded attempts."""
    tasks      = _task_rows(attempts, k)
    total_cost = sum(a.cost_usd for a in attempts)
    solved     = sum(1 for t in tasks if t["passed"])
    by_tag: dict[str, list[float]] = {}
    for task in tasks:
        for tag in (tags or {}).get(task["task"], ()):
            by_tag.setdefault(tag, []).append(task["pass_at_1"])
    low, high = bootstrap_ci([t["pass_at_1"] for t in tasks])
    return {
        "signals_in_failed_attempts": _signal_totals(attempts, False),
        "signals_in_passed_attempts": _signal_totals(attempts, True),
        "stop_reasons":               dict(sorted(Counter(a.stop or "none" for a in attempts if not a.passed).items())),
        "tokens":                     {key: sum(a.usage.get(key, 0) for a in attempts) for key in ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens")},
        "environment":                merge_environments([a.environment for a in attempts if a.environment]),
        "warnings":                   summary_warnings(tasks),
        **_audit_figures(attempts),
        "mean_pass_at_1_ci":    [low, high],
        "by_tag":               {tag: {"tasks": len(rates), "mean_pass_at_1": _mean(rates)} for tag, rates in sorted(by_tag.items())},
        "k":                    k,
        "tasks":                tasks,
        "attempts":             len(attempts),
        "passed_attempts":      sum(1 for a in attempts if a.passed),
        "tasks_solved":         solved,
        "mean_pass_at_1":       _mean([t["pass_at_1"] for t in tasks]),
        "mean_pass_at_k":       _mean([t[f"pass_at_{k}"] for t in tasks]),
        "mean_pass_hat_k":      _mean([t[f"pass_hat_{k}"] for t in tasks]),
        "total_cost_usd":       total_cost,
        "cost_per_solved_task": total_cost / solved if solved else None,
    }


def _render_tasks(summary: dict[str, Any]) -> list[str]:
    """The per-task table."""
    k      = summary["k"]
    header = f"{'task':<28} {'n':>3} {'pass':>4} {'pass@1':>7} {f'pass@{k}':>7} {f'pass^{k}':>7} {'cost USD':>9} {'mean s':>7}"
    lines  = [header, "-" * len(header)]
    for t in summary["tasks"]:
        lines.append(
            f"{t['task']:<28} {t['attempts']:>3} {t['passed']:>4} {t['pass_at_1']:>7.2f} "
            f"{t[f'pass_at_{k}']:>7.2f} {t[f'pass_hat_{k}']:>7.2f} {t['cost_usd']:>9.4f} {t['mean_seconds']:>7.1f}"
        )
    return lines


def _render_notes(summary: dict[str, Any]) -> list[str]:
    """Environment, audit and warnings, which qualify how far the figures can be trusted."""
    lines: list[str] = []
    if summary["environment"]:
        lines += ["", "environment: " + ", ".join(f"{key}={value}" for key, value in summary["environment"].items())]
    if summary["audit_flagged_attempts"]:
        kinds  = ", ".join(f"{kind} {count}" for kind, count in summary["audit_kinds"].items())
        lines += ["", f"audit: {summary['audit_flagged_attempts']} of {summary['attempts']} attempts looked for an answer outside the task ({summary['audit_flagged_passed']} of them passed): {kinds}"]
    lines += [f"WARNING: {warning}" for warning in summary["warnings"]]
    return lines


def render(summary: dict[str, Any]) -> str:
    """The summary as a plain text table."""
    k     = summary["k"]
    lines = _render_tasks(summary)
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
        f"mean pass@{k} {summary['mean_pass_at_k']:.2f}, mean pass^{k} {summary['mean_pass_hat_k']:.2f}, "
        f"total cost ${summary['total_cost_usd']:.4f}, "
        f"cost per solved task {'n/a' if per_solved is None else f'${per_solved:.4f}'}"
    )
    return "\n".join(lines + _render_notes(summary))


# ---------------------------------------------------------------------------
# Environment record
# ---------------------------------------------------------------------------


def _git_output(args: list[str]) -> str | None:
    """Standard output of a git command run in the repository this script belongs to; None when it fails."""
    try:
        done = subprocess.run(["git", "-C", str(REPO_ROOT), *args], capture_output=True, text=True, timeout=30, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return done.stdout.strip() if done.returncode == 0 else None


def memory_total_mb() -> int | None:
    """Physical memory in MiB, None where the system does not say."""
    try:
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") // (1024 * 1024)
    except (ValueError, OSError, AttributeError):
        return None


def run_environment(options: argparse.Namespace) -> dict[str, Any]:
    """The facts of this run that change scores and do not depend on the task."""
    status = _git_output(["status", "--porcelain"])
    return {
        "cpu_count":        os.cpu_count(),
        "memory_total_mb":  memory_total_mb(),
        "nerdvana_version": __version__,
        "git_commit":       _git_output(["rev-parse", "HEAD"]) or "",
        "git_dirty":        None if status is None else bool(status),
        "model":            options.model,
        "provider":         options.provider,
        "sandbox":          options.sandbox,
        "network":          not options.no_network,
        "isolate":          options.isolate,
        "gate":             options.gate,
        "overrides":        ", ".join(options.set),
    }


def attempt_environment(task: Task, run_env: dict[str, Any], report: dict[str, Any]) -> dict[str, Any]:
    """The run's facts plus this task's limits and the model that actually answered."""
    return {
        **run_env,
        "model":        str(report.get("model") or run_env.get("model", "")),
        "provider":     str(report.get("provider") or run_env.get("provider", "")),
        "timeout_s":    task.timeout,
        "max_turns":    task.max_turns,
        "max_cost_usd": task.max_cost_usd,
    }


# ---------------------------------------------------------------------------
# Transcript audit
# ---------------------------------------------------------------------------


def tool_events(stdout: str) -> list[dict[str, Any]]:
    """The ``tool_start`` events of a ``stream-json`` run: tool name and the head of its arguments."""
    events: list[dict[str, Any]] = []
    for line in stdout.splitlines():
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict) and data.get("type") == "tool_start":
            events.append(data)
    return events


def _lookup_kinds(name: str, summary: str, task_id: str) -> list[str]:
    """The kinds of answer lookup one tool call shows."""
    kinds = []
    if name in _WEB_TOOLS:
        kinds.append("web_tool")
    if _GIT_HISTORY.search(summary):
        kinds.append("git_history")
    if _GIT_INTERNALS.search(summary):
        kinds.append("git_internals")
    if _PACKAGE_FETCH.search(summary):
        kinds.append("package_download")
    if _URL_FETCH.search(summary):
        hosts = _URL_HOST.findall(summary)
        if not hosts or any(host.lower() not in REGISTRY_HOSTS for host in hosts):
            kinds.append("network_fetch")
    if re.search(rf"benchmarks[\\/]+solutions|solutions[\\/]+{re.escape(task_id)}", summary):
        kinds.append("solutions_read")
    return kinds


def audit_events(events: list[dict[str, Any]], task_id: str) -> list[dict[str, str]]:
    """Findings of solution-lookup behaviour in the tool calls of one attempt.

    Only the head of each call's arguments is seen (the loop reports at most 80 characters), so a long
    command can hide a lookup. A finding is evidence to read, not a verdict.
    """
    findings = []
    for event in events:
        name, summary = str(event.get("name", "")), str(event.get("summary", ""))
        findings += [{"kind": kind, "tool": name, "excerpt": summary} for kind in _lookup_kinds(name, summary, task_id)]
    return findings


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


def isolate_repository(workdir: Path) -> str:
    """Replace the working directory's git history by a fresh repository with one commit.

    The agent then finds no upstream history, tags, branches or reflog to read a later fix from.
    Returns an error text, empty on success.
    """
    git_dir = workdir / ".git"
    try:
        if git_dir.is_dir():
            shutil.rmtree(git_dir)
        elif git_dir.exists():
            git_dir.unlink()
    except OSError as exc:
        return f"could not remove the repository history: {exc}"
    steps = (
        ("init",   ["init", "--quiet"]),
        ("add",    ["add", "-A"]),
        ("commit", [*GIT_IDENTITY, "commit", "--quiet", "--no-verify", "--allow-empty", "-m", "task snapshot"]),
    )
    for name, args in steps:
        done = subprocess.run(["git", *args], cwd=workdir, capture_output=True, text=True, check=False)
        if done.returncode:
            return f"isolation failed at git {name}: {done.stderr.strip()[:300]}"
    return ""


def prepare(task: Task, workdir: Path, isolate: bool = False) -> str:
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
    if isolate and (problem := isolate_repository(workdir)):
        return problem
    if task.setup:
        done = _shell(task.setup, workdir, SETUP_TIMEOUT)
        if done is None or done.returncode:
            return "setup command failed or timed out"
    return ""


def network_off_config(root: Path) -> Path:
    """Write the configuration file that cuts the agent's network access; returns its path.

    ``--set`` cannot change the ``sandbox`` or ``permissions`` sections, so the file is the user's own
    configuration (the first of ``NERDVANA_CONFIG`` and the user and legacy config files) with the
    sandbox required, the network limited to the package registries, and the in-process web tools
    denied. The hosts that hold the answers are not among the registries, so they are unreachable while
    a task runs. A config file named with ``--config`` replaces the search, so what the user configured
    is carried over by copy.
    """
    sources = [os.environ.get("NERDVANA_CONFIG", ""), str(core_paths.user_config_path()), str(core_paths.legacy_config_path())]
    base    = next((Path(source) for source in sources if source and os.path.exists(source)), None)
    data    = yaml.safe_load(base.read_text(encoding="utf-8")) if base else {}
    data    = data or {}
    if not isinstance(data, dict):
        raise TaskError(f"{base}: top level must be a mapping")
    permissions = dict(data.get("permissions") or {})
    deny        = [str(rule) for rule in permissions.get("always_deny") or []]
    permissions["always_deny"] = deny + [tool for tool in sorted(_WEB_TOOLS) if tool not in deny]
    data["permissions"] = permissions
    data["sandbox"]     = {**(data.get("sandbox") or {}), **evaluation_network()}
    target = root / "no-network.yml"
    target.write_text(yaml.safe_dump(data), encoding="utf-8")
    target.chmod(0o600)
    return target


def agent_command(task: Task, options: argparse.Namespace) -> list[str]:
    """The ``nerdvana run`` invocation for a task; the events stream is what the transcript audit reads."""
    prompt = task.prompt + (LEARN_INSTRUCTION if getattr(options, "learn", False) else "")
    command = [
        sys.executable, "-c", "from nerdvana_cli.main import app; app()", "run", prompt,
        "--output-format", "stream-json",
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


def _record_report(result: Attempt, agent: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    """Copy what the agent's result object says into *result*; returns the object."""
    report           = parse_result(agent.stdout)
    result.exit_code = agent.returncode
    result.stop      = str(report.get("subtype", ""))
    result.turns     = int(report.get("num_turns", 0) or 0)
    result.cost_usd  = float(report.get("total_cost_usd", 0.0) or 0.0)
    result.signals   = {str(k): int(v) for k, v in (report.get("signals") or {}).items()}
    result.usage     = {str(k): int(v) for k, v in (report.get("usage") or {}).items()}
    result.audit     = audit_events(tool_events(agent.stdout), result.task_id)
    if not report:
        result.error = f"no result object in the output (exit {agent.returncode}): {agent.stderr.strip()[-300:]}"
    elif report.get("is_error"):
        result.error = str(report.get("error") or report.get("result") or "")[-300:]
    return report


def run_attempt(
    task: Task, number: int, options: argparse.Namespace, root: Path,
    *, run_env: dict[str, Any] | None = None, config: Path | None = None,
) -> Attempt:
    """One attempt: the candidates the method produces, and the best of them.

    Every candidate is run, so the method spends the same on every task; the
    attempt is judged by the best of them, and the cost it reports is what all
    of them spent together. The winner is picked among anonymous ids, so the
    order the candidates came in does not decide it.
    """
    candidates = options.method.candidates
    results: list[tuple[int, Attempt]] = []
    spent = 0.0
    for index in range(1, candidates + 1):
        result = run_candidate(task, number, index, candidates, options, root, run_env=run_env, config=config)
        spent += result.cost_usd
        results.append((index, result))
    best = _best_candidate(results, task.id, options.method.name)
    if best is None:
        raise TaskError(f"{task.id}: the method produced no candidate")
    _, attempt = best
    attempt.cost_usd   = spent
    attempt.candidates = candidates
    _keep_learning(task, attempt, options)
    return attempt


def _best_candidate(results: list[tuple[int, Attempt]], task_id: str, mode: str) -> tuple[int, Attempt] | None:
    """The candidate that wins, picked without letting the order decide it.

    The candidates are presented under anonymous ids, in the order of those ids,
    so a candidate that passed is not preferred for having come first.
    """
    if not results:
        return None
    selector  = AnonymizedSelector(task_id, mode=mode)
    candidates = [
        Candidate(label=f"c{index}", kind="candidate", deliverable="passed" if attempt.passed else "failed")
        for index, attempt in results
    ]

    def judge(presented: Sequence[AnonymousCandidate]) -> str:
        return next(
            (entry.anonymous_id for entry in presented if entry.deliverable == "passed"),
            presented[0].anonymous_id,
        )

    winner = selector.select(candidates, judge)
    if winner is None:
        return None
    return next((pair for pair in results if f"c{pair[0]}" == winner.label), results[0])


def _library_of(options: argparse.Namespace, task: Task) -> DomainLibrary | None:
    """The library of the task's domain, when the run has one."""
    if not options.library:
        return None
    return DomainLibrary(root=options.library, domain=domain_of(task.tags, task.domain), surface=NERDVANA_SURFACE)


def _keep_learning(task: Task, attempt: Attempt, options: argparse.Namespace) -> None:
    """Keep what the task learned, so the next task of its domain starts from it.

    Only a task that passed teaches anything: a harness that did not work is
    not kept, and a run that changed nothing has nothing to teach.
    """
    if not options.learn or not attempt.passed or not attempt.harness:
        return
    library = _library_of(options, task)
    if library is None:
        return
    library.add(f"{task.id}#{attempt.attempt}", attempt.harness, _experience_note(task, library, attempt))


def _experience_note(task: Task, library: DomainLibrary, attempt: Attempt) -> ExperienceNote:
    """The note that goes with what the task learned.

    The note names the situation and not the task: a lesson that names one task
    is not one the next task of the domain can use, and the library matches a
    lesson against a task by the situation it applies to.
    """
    domain = library.domain
    skills = sorted(path for path in attempt.harness if path.startswith(".nerdvana/skills/"))
    scripts = sorted(path for path in attempt.harness if path.startswith("scripts/"))
    kept: list[str] = []
    if skills:
        kept.append("the skills " + ", ".join(skills))
    if scripts:
        kept.append("the helper scripts " + ", ".join(scripts))
    lesson = "What made it work: " + ("; ".join(kept) if kept else "what the agent left behind") + "."

    situations = [
        fields["description"]
        for path, content in sorted(attempt.harness.items())
        for fields in [front_matter_fields(content)]
        if fields.get("description")
    ]
    cue = f"a {domain} task: " + ("; ".join(situations) if situations else "a task of the kind the domain holds")
    return ExperienceNote(
        cue      = cue,
        lesson   = lesson,
        evidence = f"the verify command passed on a {domain} task; the harness is kept whole.",
    )


def run_candidate(
    task: Task, number: int, index: int, candidates: int, options: argparse.Namespace, root: Path,
    *, run_env: dict[str, Any] | None = None, config: Path | None = None,
) -> Attempt:
    """One candidate of an attempt, in a working directory of its own."""
    if options.substrate.name == "subagent":
        return run_subagent_candidate(task, number, index, options, root, config=config)
    suffix = "" if candidates == 1 else f"-c{index}"
    workdir = root / f"{task.id}-{number}{suffix}"
    result  = Attempt(task_id=task.id, attempt=number, passed=False)
    report: dict[str, Any] = {}
    problem = prepare(task, workdir, options.isolate)
    if problem:
        result.error = problem
        return result

    before = _give_harness(task, workdir, options)
    command = agent_command(task, options) + (["--config", str(config)] if config else [])
    started = time.monotonic()
    try:
        agent = subprocess.run(
            command, cwd=workdir, capture_output=True, text=True, timeout=task.timeout, check=False,
        )
        report = _record_report(result, agent)
    except subprocess.TimeoutExpired:
        result.stop  = "timeout"
        result.error = f"agent exceeded {task.timeout}s"
    result.duration_s  = time.monotonic() - started
    result.environment = attempt_environment(task, run_env or {}, report)

    verdict       = _shell(task.verify, workdir, VERIFY_TIMEOUT)
    result.passed = verdict is not None and verdict.returncode == 0
    result.harness = changed_tree(before, read_tree(workdir))
    return result


def _give_harness(task: Task, workdir: Path, options: argparse.Namespace) -> dict[str, str]:
    """Give the run the harness its domain learned; returns the tree it started from.

    Two ways to hand it over. The shelf gives the run everything the domain
    holds, inlined as instructions -- the way the first measurement was made,
    and the way it found the shelf unhelpful. The menu gives the run the
    lessons as the files they are and a list of them to choose from, so the
    task reads the one that applies to it and the rest costs it nothing.
    """
    library = _library_of(options, task)
    if library is not None:
        entries = library.entries()
        if options.library_mode == "menu":
            give_menu(entries, workdir)
        else:
            give_tree(tree_of(entries), workdir)
    return read_tree(workdir)


def run_subagent_candidate(
    task: Task, number: int, index: int, options: argparse.Namespace, root: Path,
    *, config: Path | None = None,
) -> Attempt:
    """The task answered by a sub-agent preset, in this process.

    The preset decides the system prompt, the tools and the turn limit; the
    verify command still decides whether the task was done. The transcript
    audit is empty here: the preset's transcript is not the event stream the
    top-level run writes.
    """
    import asyncio

    from nerdvana_cli.agents.builtin import BUILTIN_AGENTS
    from nerdvana_cli.cli.bootstrap import build_subagent
    from nerdvana_cli.cli.runtime import resolve_run_provider
    from nerdvana_cli.core.config.model_routing import select_model
    from nerdvana_cli.core.config.settings import NerdvanaSettings
    from nerdvana_cli.core.delegation.subagent import run_subagent

    result  = Attempt(task_id=task.id, attempt=number, passed=False)
    workdir = root / f"{task.id}-{number}-c{index}"
    problem = prepare(task, workdir, options.isolate)
    if problem:
        result.error = problem
        return result
    preset = next((a for a in BUILTIN_AGENTS if a.agent_type == options.substrate.preset), None)
    if preset is None:
        result.error = f"no sub-agent preset named '{options.substrate.preset}'"
        return result
    before = _give_harness(task, workdir, options)
    settings = NerdvanaSettings.load(str(config) if config else None)
    settings.cwd             = str(workdir)
    settings.session.persist = False
    # The preset may name a model of its own; the model the run was told to
    # use wins over it, so a cell of the experiment stays comparable.
    model = select_model(options.model, "", preset.model, preset.category, settings.agents.categories)
    if model:
        settings.model.model = model
    if options.provider:
        settings.model.provider = options.provider
    # The provider is filled in from the model when it was not named, and the
    # key is taken from the environment; without this the sub-agent has none.
    resolve_run_provider(settings)
    started = time.monotonic()
    try:
        sub = build_subagent(settings, preset, task.prompt, f"{task.id}-{number}", preset.category)
        output, _tokens = asyncio.run(run_subagent(sub, asyncio.Event()))
        result.cost_usd = sub.cost_usd
        result.stop     = sub.stopped_for
        if not output.strip():
            result.error = f"the sub-agent said nothing (stop: {sub.stopped_for or 'unknown'})"
        elif sub.stopped_for not in ("", "completed"):
            result.error = output.strip()[-300:]
    except Exception as exc:  # noqa: BLE001 - the attempt has to say what happened
        result.error = f"{type(exc).__name__}: {exc}"[-300:]
    result.duration_s = time.monotonic() - started
    verdict       = _shell(task.verify, workdir, VERIFY_TIMEOUT)
    result.passed = verdict is not None and verdict.returncode == 0
    result.harness = changed_tree(before, read_tree(workdir))
    return result


def worst_case_cost(tasks: list[Task], attempts: int) -> float:
    """Most the run can spend if every attempt reaches its cost ceiling."""
    return sum(t.max_cost_usd for t in tasks) * attempts


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Measure the agent's success rate on fixed repository tasks.")
    parser.add_argument("tasks", type=Path, nargs="?", help="a task file or a directory of *.yml task files")
    parser.add_argument("--method", default="single", help="how the agent answers a task: single | ppc | ppc:K (the propose-probe-compose run with K candidates)")
    parser.add_argument("--substrate", default="loop", help="what answers the task: loop | subagent | subagent:PRESET")
    parser.add_argument("--benchmark", default="", help="the name of a known benchmark (short-20, long-8, ...); the tasks path is used when it is not given")
    parser.add_argument("--source", default="", help="where the tasks of an external benchmark are")
    parser.add_argument("--results-dir", type=Path, default=None, help="the directory the results are kept in; one made with another configuration is not continued")
    parser.add_argument("--library", type=Path, default=None, help="the directory the domain library is kept in; a task starts from what its domain learned")
    parser.add_argument("--library-mode", choices=("menu", "shelf"), default="menu", help="how the domain's lessons reach a task: 'menu' offers them and lets the task choose, 'shelf' hands over all of them")
    parser.add_argument("--learn", action="store_true", help="let a task leave behind what it learned, so the next task of its domain starts from it (needs --library)")
    parser.add_argument("--set", action="append", default=[], metavar="SECTION.FIELD=VALUE", help="override a setting in every attempt (nerdvana run --set), e.g. to compare compaction thresholds")
    parser.add_argument("--gate", action="store_true", help="hold the agent to each task's verify command (nerdvana run --verify) instead of checking only afterwards")
    parser.add_argument("--tag", action="append", default=[], help="run only tasks carrying this tag (repeatable)")
    parser.add_argument("--attempts", type=int, default=1, help=f"attempts per task (default 1; use {RECOMMENDED_ATTEMPTS} or more, pass^k and the intervals need repeats)")
    parser.add_argument("--k", type=int, default=0, help="k for pass@k and pass^k (default: the number of attempts)")
    parser.add_argument("--model", default="")
    parser.add_argument("--provider", default="")
    parser.add_argument("--approval-mode", default="yolo", choices=["default", "auto_edit", "yolo", "plan"])
    parser.add_argument("--sandbox", default="require", choices=["off", "auto", "require"],
                        help="confine each attempt's shell commands to its working directory (default require; off on systems without Landlock)")
    parser.add_argument("--isolate", action="store_true", help="turn each working directory into a fresh repository with one commit, so the agent finds no upstream history")
    parser.add_argument("--no-network", action="store_true", help="let the agent's shell commands reach the package registries only -- the hosts that hold the answers (github.com, huggingface.co, raw.githubusercontent.com) are unreachable -- and deny WebFetch and WebSearch; needs --sandbox require and Linux 6.7 (UDP and DNS stay open)")
    parser.add_argument("--out", type=Path, default=None, help="JSONL file the attempts are appended to (default: bench-results.jsonl, or results.jsonl in --results-dir)")
    parser.add_argument("--keep-workdirs", action="store_true", help="keep each attempt's working directory for inspection")
    parser.add_argument("--yes", action="store_true", help="spend money: run the agent (without it only the plan is printed)")
    return parser.parse_args(argv)


def option_problem(options: argparse.Namespace) -> str:
    """Why the options cannot be run, empty when they can."""
    if options.attempts < 1:
        return "--attempts must be at least 1"
    if not options.tasks and not options.benchmark:
        return "a task file or a --benchmark is needed"
    if options.substrate.startswith("subagent") and ":" not in options.substrate:
        return "--substrate subagent needs the preset: --substrate subagent:code-reviewer"
    if options.learn and not options.library:
        return "--learn needs --library: what a task learns is kept in the library"
    if options.no_network and options.sandbox != "require":
        return "--no-network needs --sandbox require"
    if options.no_network and landlock_abi() < NO_NETWORK_ABI:
        return f"--no-network needs Landlock ABI {NO_NETWORK_ABI} (Linux 6.7); this system offers {landlock_abi()}"
    return ""


def runmark_settings(options: argparse.Namespace) -> dict[str, Any]:
    """The settings that decide the run; the mark of the run is computed over them."""
    return {
        "attempts": options.attempts,
        "gate":     options.gate,
        "isolate":  options.isolate,
        "k":        options.k,
        "learn":    options.learn,
        "library":  str(options.library) if options.library else "",
        "library_mode": options.library_mode,
        "model":    options.model,
        "network":  not options.no_network,
        "provider": options.provider,
        "sandbox":  options.sandbox,
        "set":      list(options.set),
    }


def build_spec(method: BenchMethod, substrate: BenchSubstrate, benchmark: BenchBenchmark) -> BenchSpec:
    """The cell of the experiment this run is."""
    return BenchSpec(method=method, substrate=substrate, benchmark=benchmark)


def _print_plan(tasks: list[Task], options: argparse.Namespace) -> None:
    candidates = getattr(options.method, "candidates", 1)
    print(f"{len(tasks)} task(s) x {options.attempts} attempt(s) x {candidates} candidate(s); worst-case spend ${worst_case_cost(tasks, options.attempts) * candidates:.2f}")
    if options.attempts < RECOMMENDED_ATTEMPTS:
        print(f"recommended: --attempts {RECOMMENDED_ATTEMPTS} or more; one attempt per task cannot tell a change from noise, and pass^k needs repeats")


def _run_all(tasks: list[Task], options: argparse.Namespace, root: Path) -> list[Attempt]:
    """Run every attempt, appending each result line to the output file as it finishes."""
    config   = network_off_config(root) if options.no_network else None
    run_env  = run_environment(options)
    attempts: list[Attempt] = []
    for task in tasks:
        for number in range(1, options.attempts + 1):
            attempt = run_attempt(task, number, options, root, run_env=run_env, config=config)
            attempts.append(attempt)
            with options.out.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(attempt.to_record()) + "\n")
            flagged = f", audit {len(attempt.audit)}" if attempt.audit else ""
            print(f"{task.id} #{number}: {'pass' if attempt.passed else 'fail'} ({attempt.stop or 'no result'}, ${attempt.cost_usd:.4f}, {attempt.duration_s:.0f}s{flagged})", flush=True)
    return attempts


def main(argv: list[str] | None = None) -> int:
    options = parse_args(sys.argv[1:] if argv is None else argv)
    if problem := option_problem(options):
        print(f"error: {problem}", file=sys.stderr)
        return 2
    try:
        method    = BenchMethod.parse(options.method)
        substrate = BenchSubstrate.parse(options.substrate)
    except BenchError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    options.method    = method
    options.substrate = substrate
    try:
        tasks, benchmark = _tasks_of(options)
    except (TaskError, BenchError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if options.tag:
        tasks = [t for t in tasks if set(options.tag) & set(t.tags)]
        if not tasks:
            print(f"error: no task carries any of the tags {options.tag}", file=sys.stderr)
            return 2

    spec = build_spec(method, substrate, benchmark)
    mark = spec.runmark(runmark_settings(options))
    if options.results_dir:
        try:
            check_runmark(options.results_dir, mark)
        except RunmarkMismatchError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
    options.out = options.out or (options.results_dir / "results.jsonl" if options.results_dir else Path("bench-results.jsonl"))

    _print_plan(tasks, options)
    print(f"method: {method.name}{'' if method.name == 'single' else f':{method.k}'} | substrate: {substrate.name}{':' + substrate.preset if substrate.preset else ''} | benchmark: {benchmark.name} | runmark: {mark}")
    if not options.yes:
        print("dry run: pass --yes to run the agent against the real API")
        return 0

    if options.results_dir:
        # The mark is written before the run: the directory belongs to this
        # configuration from the first attempt on.
        write_runmark(options.results_dir, mark)
    root = Path(tempfile.mkdtemp(prefix="nerdvana-bench-"))
    try:
        attempts = _run_all(tasks, options, root)
    except (TaskError, yaml.YAMLError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    finally:
        if options.keep_workdirs:
            print(f"working directories kept in {root}")
        else:
            shutil.rmtree(root, ignore_errors=True)
    print()
    print(render(summarize(attempts, options.k or options.attempts, {t.id: t.tags for t in tasks})))
    return 0


def _tasks_of(options: argparse.Namespace) -> tuple[list[Task], BenchBenchmark]:
    """The tasks of the run, and the benchmark they belong to."""
    if options.benchmark:
        loaded = load_benchmark(options.benchmark, REPO_ROOT, options.source)
        tasks  = [
            Task(id=task.id, prompt=task.prompt, verify=task.verify, path=task.path, tags=task.tags)
            for task in loaded.tasks
        ]
        return tasks, loaded
    tasks = load_tasks(options.tasks)
    benchmark = BenchBenchmark(
        name    = options.tasks.stem,
        tasks   = tuple(BenchTask(id=task.id, tags=task.tags, domain=task.domain) for task in tasks),
        adapter = "nerdvana",
        source  = str(options.tasks.resolve()),
    )
    return tasks, benchmark


if __name__ == "__main__":
    raise SystemExit(main())
