"""The run store: one durable record per background run, with a lease that tells a live run from an abandoned one.

Author: 최진호
Date:   2026-10-03

A background agent task (``Agent`` with ``run_in_background``) and a supervised session (``nerdvana agents
start``) each get a directory under ``<data root>/runs/<id>/`` holding ``record.json`` (this module), the
stream-json ``run.log`` and ``stderr.log`` of a supervised run, and the result file. The record is replaced
atomically on every write, so a reader never sees a half-written one, and only the process that owns a run
writes its record while it goes on.

The owner renews the lease by writing ``heartbeat_at`` every ``HEARTBEAT_SECONDS``. A record that says
``running`` whose heartbeat is older than ``LEASE_SECONDS`` and whose owner process is gone is reported as
``orphaned``: its process died without finishing it (a restart, a crash, a kill). Such a run can be resumed
from its session transcript. The process id check cannot tell a process from a later one that reused the
number; the stale heartbeat is what keeps a recycled id from making an abandoned run look alive for long.

A record may carry an idempotency key. Creating a second record with a key that is already claimed returns
the first one, so a retried start does not run twice.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import os
import shutil
import time
import uuid
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any

from nerdvana_cli.core import paths as core_paths

logger = logging.getLogger(__name__)

HEARTBEAT_SECONDS = 5.0
LEASE_SECONDS     = 20.0

RUNNING   = "running"
SUCCEEDED = "succeeded"
FAILED    = "failed"
STOPPED   = "stopped"
ORPHANED  = "orphaned"

FINISHED = frozenset({SUCCEEDED, FAILED, STOPPED})

SESSION_KIND = "session"
TASK_KIND    = "task"

RECORD_FILE = "record.json"
LOG_FILE    = "run.log"
STDERR_FILE = "stderr.log"
RESULT_JSON = "result.json"
RESULT_TEXT = "result.txt"


@dataclass
class RunRecord:
    """What is kept about one run."""

    id:             str
    kind:           str
    prompt:         str
    cwd:            str
    started_at:     float
    heartbeat_at:   float
    owner_pid:      int
    status:         str          = RUNNING
    finished_at:    float | None = None
    result_path:    str          = ""
    log_path:       str          = ""
    session_id:     str          = ""
    resume_session: str          = ""
    key:            str          = ""
    worktree_path:  str          = ""
    worktree_root:  str          = ""
    worktree_branch: str         = ""
    worktree_start: str          = ""
    child_pid:      int          = 0
    max_cost_usd:   float        = 0.0
    cost_usd:       float        = 0.0
    approval_mode:  str          = ""
    last_signal:    str          = ""
    last_signal_at: float        = 0.0
    exit_code:      int | None   = None
    attempts:       int          = 1
    error:          str          = ""


_FIELD_NAMES = frozenset(f.name for f in fields(RunRecord))


def new_run_id(kind: str = SESSION_KIND) -> str:
    """A fresh id: ``run_`` for a supervised session; background task ids come from the agent tool."""
    return f"{'run' if kind == SESSION_KIND else 'task'}_{uuid.uuid4().hex[:8]}"


def _is_zombie(pid: int) -> bool:
    """True for a process that has ended and is only waiting for its parent to collect it (Linux; False elsewhere)."""
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    except OSError:
        return False
    return stat.rpartition(")")[2].strip().startswith("Z")


def pid_alive(pid: int) -> bool:
    """True when a process with this id is running (one this user may not signal still counts; an ended one does not)."""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return not _is_zombie(pid)


def _key_name(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:32]


class RunStore:
    """The records under the run store root."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = root if root is not None else core_paths.runs_dir()

    def run_dir(self, run_id: str) -> Path:
        """The directory of run *run_id*."""
        return self.root / run_id

    def _record_path(self, run_id: str) -> Path:
        return self.run_dir(run_id) / RECORD_FILE

    def save(self, record: RunRecord) -> None:
        """Write *record* in place of the saved one, all at once."""
        directory = self.run_dir(record.id)
        directory.mkdir(parents=True, exist_ok=True)
        temporary = directory / f"{RECORD_FILE}.{os.getpid()}.tmp"
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(asdict(record), handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, self._record_path(record.id))

    def load(self, run_id: str) -> RunRecord | None:
        """The record of *run_id*; None when there is none or it cannot be read."""
        try:
            data = json.loads(self._record_path(run_id).read_text(encoding="utf-8"))
            return RunRecord(**{name: value for name, value in data.items() if name in _FIELD_NAMES})
        except (OSError, ValueError, TypeError) as exc:
            if not isinstance(exc, FileNotFoundError):
                logger.warning("run record %s cannot be read: %s", run_id, exc)
            return None

    def all(self) -> list[RunRecord]:
        """Every readable record, newest first."""
        if not self.root.is_dir():
            return []
        records = [record for child in self.root.iterdir() if child.is_dir() and (record := self.load(child.name)) is not None]
        return sorted(records, key=lambda record: record.started_at, reverse=True)

    def resolve(self, prefix: str) -> RunRecord | None:
        """The record whose id is *prefix*, or the only one that starts with it."""
        exact = self.load(prefix)
        if exact is not None:
            return exact
        matches = [record for record in self.all() if record.id.startswith(prefix)]
        return matches[0] if len(matches) == 1 else None

    def create(self, record: RunRecord) -> tuple[RunRecord, bool]:
        """Save a new record; ``(existing, False)`` instead when its key already names a run.

        The record is written before the key is claimed, and the claim is one atomic link, so of two
        simultaneous starts with the same key exactly one wins and the other gets the winner's record.
        """
        self.save(record)
        if not record.key:
            return record, True
        claim = self.root / "keys" / _key_name(record.key)
        claim.parent.mkdir(parents=True, exist_ok=True)
        holder = claim.with_name(f"{claim.name}.{record.id}")
        holder.write_text(record.id, encoding="utf-8")
        try:
            os.link(holder, claim)
        except FileExistsError:
            existing = self.load(claim.read_text(encoding="utf-8").strip())
            if existing is not None:
                shutil.rmtree(self.run_dir(record.id), ignore_errors=True)
                return existing, False
            claim.unlink(missing_ok=True)
            os.link(holder, claim)
        finally:
            holder.unlink(missing_ok=True)
        return record, True

    def update(self, run_id: str, **values: Any) -> RunRecord | None:
        """Change fields of a saved record; None when it is gone."""
        record = self.load(run_id)
        if record is None:
            return None
        for name, value in values.items():
            setattr(record, name, value)
        self.save(record)
        return record

    def touch(self, run_id: str) -> None:
        """Renew the lease of *run_id*."""
        self.update(run_id, heartbeat_at=time.time())

    def remove(self, record: RunRecord) -> None:
        """Delete the record, its files and its key claim."""
        if record.key:
            (self.root / "keys" / _key_name(record.key)).unlink(missing_ok=True)
        shutil.rmtree(self.run_dir(record.id), ignore_errors=True)

    def effective_status(self, record: RunRecord, now: float | None = None) -> str:
        """The status to show: ``orphaned`` for a run that says it is running but has been abandoned."""
        if record.status != RUNNING:
            return record.status
        stale = (time.time() if now is None else now) - record.heartbeat_at > LEASE_SECONDS
        return ORPHANED if stale and not pid_alive(record.owner_pid) else RUNNING

    def write_result(self, run_id: str, content: str, name: str = RESULT_TEXT) -> str:
        """Save a run's result next to its record and return the path."""
        path = self.run_dir(run_id) / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return str(path)


def read_lines(path: Path, offset: int = 0) -> tuple[list[str], int]:
    """The complete lines of *path* from byte *offset* on, and the offset after the last of them."""
    lines: list[str] = []
    try:
        with path.open("rb") as handle:
            handle.seek(offset)
            for raw in handle:
                if not raw.endswith(b"\n"):
                    break
                offset += len(raw)
                lines.append(raw.decode("utf-8", errors="replace").rstrip("\n"))
    except FileNotFoundError:
        pass
    return lines, offset


class TaskRecorder:
    """Keeps the record of one background agent task: written at its start, renewed while it runs, closed at its end.

    A store that cannot be written (full disk, no permission) never stops the task: the failure is logged once
    and the task goes on without a record.
    """

    def __init__(self, store: RunStore, record: RunRecord) -> None:
        self.store  = store
        self.record = record
        self._warned = False

    @classmethod
    def start(cls, task_id: str, prompt: str, cwd: str, worktree: Any = None, store: RunStore | None = None) -> TaskRecorder | None:
        """Record the start of task *task_id*; None when the store cannot be written."""
        now    = time.time()
        record = RunRecord(id=task_id, kind=TASK_KIND, prompt=prompt, cwd=cwd, started_at=now, heartbeat_at=now, owner_pid=os.getpid(), session_id=task_id)
        if worktree is not None:
            record.worktree_path, record.worktree_root = worktree.path, worktree.root
            record.worktree_branch, record.worktree_start = worktree.branch, worktree.start
        recorder = cls(store or RunStore(), record)
        try:
            recorder.store.save(record)
        except OSError as exc:
            logger.warning("background task %s is not recorded: %s", task_id, exc)
            return None
        return recorder

    def _write(self, **values: Any) -> None:
        try:
            self.store.update(self.record.id, **values)
        except OSError as exc:
            if not self._warned:
                self._warned = True
                logger.warning("run record %s cannot be written: %s", self.record.id, exc)

    async def keep_lease(self) -> None:
        """Renew the lease until cancelled."""
        while True:
            await asyncio.sleep(HEARTBEAT_SECONDS)
            self._write(heartbeat_at=time.time())

    def finish(self, status: str, output: str, error: str, cost_usd: float) -> None:
        """Close the record with how the task ended and what it produced."""
        result = ""
        with contextlib.suppress(OSError):
            result = self.store.write_result(self.record.id, output or error)
        self._write(status=status, finished_at=time.time(), heartbeat_at=time.time(), result_path=result, cost_usd=cost_usd, error=error)
