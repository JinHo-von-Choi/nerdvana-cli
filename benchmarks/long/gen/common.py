"""Shared pieces of the long-horizon task generators.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

MAX_TURNS    = 80
MAX_COST_USD = 1.0
TIMEOUT      = 1800

SUBJECTS = ["the service", "this component", "the scheduler", "the gateway", "the ledger", "the worker pool",
            "the batch job", "the cache layer", "the review board", "the operations team", "the platform group"]
VERBS    = ["reconciles", "tracks", "forwards", "validates", "records", "defers", "samples", "retries", "audits", "archives"]
OBJECTS  = ["pending requests", "stale entries", "queued messages", "settled invoices", "partial updates",
            "expired tokens", "incoming batches", "unmatched records", "regional totals", "scheduled windows"]
CLAUSES  = ["when the upstream feed lags behind", "once the nightly window closes", "unless an operator intervenes",
            "after the configured grace period", "while the backlog stays below the soft limit",
            "so that downstream consumers see a stable view", "before the next reconciliation pass starts"]


@dataclass
class TaskBuild:
    """One generated task: its YAML mapping, starting repository and reference solution overlay."""

    task_id:  str
    task:     dict[str, Any]
    fixture:  dict[str, str]
    solution: dict[str, str]
    reading:  list[str] = field(default_factory=list)


def rng_for(task_id: str) -> random.Random:
    """A random generator seeded from the task id alone, so every run produces the same bytes."""
    return random.Random(int(hashlib.sha256(task_id.encode()).hexdigest()[:12], 16))


def sentence(rng: random.Random) -> str:
    """One filler sentence that states no fact a task depends on."""
    return f"{rng.choice(SUBJECTS).capitalize()} {rng.choice(VERBS)} {rng.choice(OBJECTS)} {rng.choice(CLAUSES)}."


def prose(rng: random.Random, sentences: int) -> str:
    """Filler paragraph of the given number of sentences."""
    return " ".join(sentence(rng) for _ in range(sentences))


def sha(text: str) -> str:
    """Hex SHA-256 of a text."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def canonical(value: Any) -> str:
    """Stable JSON text of a value, used for hashing structured answers."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def digest(value: Any) -> str:
    """Short digest of a structured value; the checkers embed these instead of the answers."""
    return sha(canonical(value))[:16]


def fill(template: str, **values: str) -> str:
    """Substitute ``__NAME__`` markers in a script template."""
    for key, value in values.items():
        template = template.replace(f"__{key.upper()}__", value)
    return template


def snake(name: str) -> str:
    """camelCase to snake_case."""
    return re.sub(r"(?<=[a-z0-9])([A-Z])", lambda m: "_" + m.group(1).lower(), name).lower()


def camel(tokens: list[str]) -> str:
    """Name tokens to camelCase."""
    return tokens[0] + "".join(t.capitalize() for t in tokens[1:])


def run_python(files: dict[str, str], code: str) -> str:
    """Write *files* to a scratch directory, run ``python -c code`` there and return stdout.

    The generators use this to take the expected output of a fixture from the fixture's own code.
    """
    with tempfile.TemporaryDirectory() as tmp:
        for path, text in files.items():
            target = Path(tmp) / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
        env    = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
        result = subprocess.run([sys.executable, "-c", code], cwd=tmp, capture_output=True, text=True, env=env, check=True)  # noqa: S603
    return result.stdout
