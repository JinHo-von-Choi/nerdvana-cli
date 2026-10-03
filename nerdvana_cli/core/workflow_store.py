"""Where a workflow run keeps its results, so a failed or interrupted run can be resumed.

Author: 최진호
Date:   2026-10-03

    ~/.nerdvana/workflows/runs/<run-id>/run.json         what the run is: workflow, inputs, status, cost
    ~/.nerdvana/workflows/runs/<run-id>/step-<id>.json   the units (one agent run each) a step has finished

Every file is replaced atomically, so a run killed in the middle never leaves half a file. A unit is
stored under a key that hashes everything it depends on (its prompt and the results of the steps it
needs); on resume a unit whose key is unchanged is not run again.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
from datetime import datetime
from pathlib import Path
from typing import Any

from nerdvana_cli.core import paths as core_paths
from nerdvana_cli.core.workflow_text import WorkflowError

RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")

OK = "ok"


def write_json_atomic(path: Path, data: Any) -> None:
    """Write *data* as JSON so that *path* holds either the old file or the whole new one."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def digest(value: Any) -> str:
    """A stable hash of anything JSON can hold."""
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def new_run_id(now: datetime | None = None) -> str:
    """A run id that sorts by start time: ``20261003-141500-a1b2c3``."""
    return f"{(now or datetime.now()):%Y%m%d-%H%M%S}-{secrets.token_hex(3)}"


class RunStore:
    """The directory of one run."""

    def __init__(self, run_id: str, root: Path | None = None) -> None:
        if not RUN_ID.match(run_id):
            raise WorkflowError(f"{run_id!r} is not a run id")
        self.run_id    = run_id
        self.directory = (root or core_paths.workflow_runs_dir()) / run_id

    def exists(self) -> bool:
        return (self.directory / "run.json").is_file()

    def read_meta(self) -> dict[str, Any]:
        """The run's record; WorkflowError when there is none."""
        try:
            data = json.loads((self.directory / "run.json").read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise WorkflowError(f"no stored run {self.run_id}: {exc}") from exc
        if not isinstance(data, dict):
            raise WorkflowError(f"run {self.run_id}: run.json is damaged")
        return data

    def write_meta(self, meta: dict[str, Any]) -> None:
        write_json_atomic(self.directory / "run.json", meta)

    def load_units(self, step_id: str) -> dict[str, dict[str, Any]]:
        """The finished units of a step by key; a missing or damaged file means none."""
        try:
            data = json.loads((self.directory / f"step-{step_id}.json").read_text(encoding="utf-8"))
            units = data["units"]
        except (OSError, ValueError, KeyError, TypeError):
            return {}
        return {unit["key"]: unit for unit in units if isinstance(unit, dict) and unit.get("status") == OK and "key" in unit}

    def save_units(self, step_id: str, units: list[dict[str, Any]]) -> None:
        write_json_atomic(self.directory / f"step-{step_id}.json", {"id": step_id, "units": units})
