"""Background supervision: start ``nerdvana run`` detached, watch it, stop it, resume it, clean up after it.

Author: 최진호
Date:   2026-10-03

``start_run`` writes the run record (core/state/run_store.py), makes a git worktree for it when asked, and starts a
monitor process (``python -m nerdvana_cli.cli.supervisor <id>``) in a session of its own, so it outlives the
terminal that started it. The monitor starts the run, ``nerdvana run --output-format stream-json``, in its own
process group with the output going to the run's log, and until the run ends it renews the record's lease,
reads the log for the session id, the cost so far and the last event, and obeys SIGTERM by ending the run's
process group (SIGTERM, then SIGKILL after the grace period). When the run ends it saves the result and the
final status. The monitor is the only writer of a record while its run goes on.

``$NERDVANA_AGENTS_COMMAND`` replaces the command that stands for ``nerdvana`` (split like a shell line), which
is how the tests run a fake one.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shlex
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from nerdvana_cli.core.delegation.worktree import Worktree, WorktreeError, create_worktree, has_changes, remove_worktree
from nerdvana_cli.core.loop import cancellation
from nerdvana_cli.core.loop.cancellation import stop_process_group
from nerdvana_cli.core.state import run_store
from nerdvana_cli.core.state.run_store import (
    FAILED,
    FINISHED,
    LOG_FILE,
    ORPHANED,
    RESULT_JSON,
    RUNNING,
    SESSION_KIND,
    STDERR_FILE,
    STOPPED,
    SUCCEEDED,
    TASK_KIND,
    RunRecord,
    RunStore,
    new_run_id,
    pid_alive,
    read_lines,
)
from nerdvana_cli.core.state.session import SessionStorage

logger = logging.getLogger(__name__)

COMMAND_ENV = "NERDVANA_AGENTS_COMMAND"

RESUME_PROMPT = "The previous run was interrupted. Continue the task from where this conversation left off."

MONITOR_LOG = "monitor.log"

_TICK_SECONDS = 0.5
_POLL_SECONDS = 0.1


class SupervisorError(RuntimeError):
    """A run cannot be started, stopped or resumed as asked."""


def agents_command() -> list[str]:
    """The command that stands for ``nerdvana``: ``$NERDVANA_AGENTS_COMMAND`` when set, else this interpreter's module."""
    override = os.environ.get(COMMAND_ENV, "").strip()
    return shlex.split(override) if override else [sys.executable, "-m", "nerdvana_cli.main"]


def work_dir(record: RunRecord) -> str:
    """Where the run works: its worktree while that exists, else the directory it was started for."""
    return record.worktree_path if record.worktree_path and Path(record.worktree_path).is_dir() else record.cwd


def build_run_command(record: RunRecord) -> list[str]:
    """The ``nerdvana run`` command line of *record*; a resumed run continues the recorded session."""
    command = [*agents_command(), "run", "--cwd", work_dir(record), "--output-format", "stream-json"]
    if record.approval_mode:
        command += ["--approval-mode", record.approval_mode]
    if record.max_cost_usd > 0:
        command += ["--max-cost-usd", f"{record.max_cost_usd:g}", "--require-price"]
    if record.resume_session:
        return [*command, "--resume", record.resume_session, "--", RESUME_PROMPT]
    return [*command, "--", record.prompt]


# ---------------------------------------------------------------------------
# Reading a run's log
# ---------------------------------------------------------------------------


@dataclass
class LogScan:
    """What the monitor has learned from the run's stream-json log so far."""

    offset:      int                    = 0
    session_id:  str                    = ""
    cost_usd:    float                  = 0.0
    last_signal: str                    = ""
    result:      dict[str, Any] | None  = None

    def read(self, path: Path) -> None:
        """Take in the lines written since the last call."""
        lines, self.offset = read_lines(path, self.offset)
        for line in lines:
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if isinstance(event, dict):
                self._take(event)

    def _take(self, event: dict[str, Any]) -> None:
        kind = str(event.get("type", ""))
        if kind == "system" and event.get("session_id"):
            self.session_id = str(event["session_id"])
        elif kind == "request":
            self.cost_usd += _number(event.get("cost_usd"))
        elif kind == "result":
            self.result = event
        if kind and kind != "context":
            self.last_signal = f"tool {event.get('name', '')}".strip() if kind == "tool_start" else kind


def _number(value: Any) -> float:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0.0


# ---------------------------------------------------------------------------
# The monitor
# ---------------------------------------------------------------------------


async def _launch(record: RunRecord, store: RunStore) -> asyncio.subprocess.Process:
    """Start the run with its output going to the run's log files."""
    directory = store.run_dir(record.id)
    with (directory / LOG_FILE).open("ab") as out, (directory / STDERR_FILE).open("ab") as err:
        return await asyncio.create_subprocess_exec(
            *build_run_command(record), cwd=work_dir(record), stdin=asyncio.subprocess.DEVNULL, stdout=out, stderr=err, start_new_session=True,
        )


def _save_progress(store: RunStore, record: RunRecord, scan: LogScan) -> bool:
    """Renew the lease and save what the log showed; False when the record no longer says the run is going on."""
    saved = store.update(
        record.id, heartbeat_at=time.time(), session_id=scan.session_id or record.session_id, cost_usd=scan.cost_usd,
        last_signal=scan.last_signal, last_signal_at=time.time(),
    )
    return saved is not None and saved.status == RUNNING


async def _watch(store: RunStore, record: RunRecord, process: asyncio.subprocess.Process, scan: LogScan, stop: asyncio.Event) -> None:
    """Renew the lease and read the log until the run ends; a stop asked for, by signal or by a record that no longer says running, ends the run's process group."""
    ended     = asyncio.ensure_future(process.wait())
    asked     = asyncio.ensure_future(stop.wait())
    log       = store.run_dir(record.id) / LOG_FILE
    last_beat = time.monotonic()
    try:
        while not (ended.done() or asked.done()):
            await asyncio.wait({ended, asked}, timeout=_TICK_SECONDS, return_when=asyncio.FIRST_COMPLETED)
            known = scan.session_id
            scan.read(log)
            if scan.session_id != known or time.monotonic() - last_beat >= run_store.HEARTBEAT_SECONDS:
                if not _save_progress(store, record, scan):
                    stop.set()
                last_beat = time.monotonic()
        if not ended.done():
            await stop_process_group(process)
        scan.read(log)
    finally:
        asked.cancel()
        ended.cancel()


def _close_record(store: RunStore, record: RunRecord, scan: LogScan, code: int, stopped: bool) -> None:
    """Save the result and the final status of a run that has ended."""
    result = scan.result or {"type": "result", "subtype": "error_during_run", "is_error": True, "error": f"the run ended with exit code {code} and no result"}
    path   = store.write_result(record.id, json.dumps(result, ensure_ascii=False, indent=2), RESULT_JSON)
    status = STOPPED if stopped else SUCCEEDED if code == 0 else FAILED
    store.update(
        record.id, status=status, finished_at=time.time(), heartbeat_at=time.time(), result_path=path, exit_code=code,
        session_id=scan.session_id or record.session_id, cost_usd=_number(result.get("total_cost_usd")) or scan.cost_usd,
        last_signal=f"result {result.get('subtype', status)}", last_signal_at=time.time(), error=str(result.get("error", "")),
    )


async def monitor(run_id: str, store: RunStore | None = None) -> int:
    """Run the record *run_id* to its end; the exit code of this process (0 when the record was closed)."""
    store  = store or RunStore()
    record = store.load(run_id)
    if record is None:
        return 2
    if record.status != RUNNING:
        return 0  # stopped before this monitor got going
    record = store.update(run_id, owner_pid=os.getpid(), heartbeat_at=time.time(), error="") or record
    stop = asyncio.Event()
    asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, stop.set)
    scan = LogScan()
    try:
        process = await _launch(record, store)
        store.update(run_id, child_pid=process.pid)
        await _watch(store, record, process, scan, stop)
        code = process.returncode if process.returncode is not None else -1
        _close_record(store, record, scan, code, stopped=stop.is_set() and code != 0)
    except Exception as exc:  # noqa: BLE001
        logger.exception("monitor of %s failed", run_id)
        store.update(run_id, status=FAILED, finished_at=time.time(), error=f"{type(exc).__name__}: {exc}")
        return 1
    return 0


def spawn_monitor(store: RunStore, run_id: str) -> int:
    """Start the monitor of *run_id* detached from this terminal; its pid."""
    directory = store.run_dir(run_id)
    with (directory / MONITOR_LOG).open("ab") as log:
        process = subprocess.Popen(
            [sys.executable, "-m", "nerdvana_cli.cli.supervisor", run_id],
            stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True, close_fds=True,
        )
    return process.pid


# ---------------------------------------------------------------------------
# Start, resume, stop, clean
# ---------------------------------------------------------------------------


def _launch_monitor(store: RunStore, run_id: str) -> None:
    try:
        spawn_monitor(store, run_id)
    except OSError as exc:
        store.update(run_id, status=FAILED, finished_at=time.time(), error=f"cannot start the monitor: {exc}")
        raise SupervisorError(f"cannot start the monitor: {exc}") from exc


def start_run(
    prompt:        str,
    cwd:           str,
    worktree:      bool         = False,
    max_cost_usd:  float        = 0.0,
    key:           str          = "",
    approval_mode: str          = "",
    store:         RunStore | None = None,
) -> tuple[RunRecord, bool]:
    """Start *prompt* as a supervised run in *cwd*; the record, and whether a run was started.

    A *key* that already names a run returns that run's record and starts nothing.
    """
    store  = store or RunStore()
    run_id = new_run_id(SESSION_KIND)
    now    = time.time()
    record = RunRecord(
        id=run_id, kind=SESSION_KIND, prompt=prompt, cwd=cwd, started_at=now, heartbeat_at=now, owner_pid=0, key=key,
        max_cost_usd=max_cost_usd, approval_mode=approval_mode, log_path=str(store.run_dir(run_id) / LOG_FILE),
    )
    record, created = store.create(record)
    if not created:
        return record, False
    if worktree:
        try:
            made = create_worktree(cwd, prompt)
        except WorktreeError as exc:
            store.update(run_id, status=FAILED, finished_at=time.time(), error=str(exc))
            raise SupervisorError(str(exc)) from exc
        store.update(run_id, worktree_path=made.path, worktree_root=made.root, worktree_branch=made.branch, worktree_start=made.start)
    _launch_monitor(store, run_id)
    return store.load(run_id) or record, True


def resume_run(prefix: str, store: RunStore | None = None) -> RunRecord:
    """Start an orphaned, stopped or failed run again from its session transcript."""
    store  = store or RunStore()
    record = _find(store, prefix)
    status = store.effective_status(record)
    if status not in (ORPHANED, STOPPED, FAILED):
        raise SupervisorError(f"run {record.id} is {status}; only an orphaned, stopped or failed run can be resumed")
    if pid_alive(record.child_pid) and status == ORPHANED:
        raise SupervisorError(f"the process of run {record.id} (pid {record.child_pid}) is still alive; stop it first with: nerdvana agents stop {record.id}")
    if not record.session_id or not SessionStorage(session_id=record.session_id).load_messages():
        raise SupervisorError(f"cannot resume run {record.id}: it has no session transcript ({record.session_id or 'no session recorded'})")
    store.update(
        record.id, kind=SESSION_KIND, status=RUNNING, resume_session=record.session_id, attempts=record.attempts + 1, owner_pid=0, child_pid=0,
        heartbeat_at=time.time(), finished_at=None, exit_code=None, error="",
    )
    _launch_monitor(store, record.id)
    return store.load(record.id) or record


def _find(store: RunStore, prefix: str) -> RunRecord:
    record = store.resolve(prefix)
    if record is None:
        raise SupervisorError(f"no run '{prefix}' (or the prefix is ambiguous)")
    return record


def _end_group(pid: int, grace: float) -> None:
    """End the process group *pid* leads: SIGTERM, then SIGKILL once *grace* seconds have passed."""
    for sent, wait in ((signal.SIGTERM, grace), (signal.SIGKILL, 2.0)):
        if not pid_alive(pid):
            return
        try:
            os.killpg(pid, sent)
        except (ProcessLookupError, PermissionError):
            return
        deadline = time.monotonic() + wait
        while pid_alive(pid) and time.monotonic() < deadline:
            time.sleep(_POLL_SECONDS)


def _ask_monitor_to_stop(store: RunStore, record: RunRecord) -> RunRecord | None:
    """SIGTERM the run's monitor and wait for it to close the record; None when it does not within the grace period."""
    if record.owner_pid <= 0:
        return None
    try:
        os.kill(record.owner_pid, signal.SIGTERM)
    except ProcessLookupError:
        return None
    deadline = time.monotonic() + cancellation.CANCEL_GRACE_SECONDS + 5.0
    while time.monotonic() < deadline:
        current = store.load(record.id)
        if current is None or current.status != RUNNING:
            return current
        time.sleep(_POLL_SECONDS)
    return None


def stop_run(prefix: str, store: RunStore | None = None) -> RunRecord:
    """Stop a run: its monitor ends it gracefully, and is killed with the run when the grace period passes; an abandoned run is marked stopped."""
    store  = store or RunStore()
    record = _find(store, prefix)
    status = store.effective_status(record)
    if status in FINISHED:
        raise SupervisorError(f"run {record.id} already ended ({status})")
    if record.kind == TASK_KIND and status == RUNNING:
        raise SupervisorError(f"run {record.id} is a background task of the session with pid {record.owner_pid}; stop it there (TaskStop)")
    if status == RUNNING and (closed := _ask_monitor_to_stop(store, record)) is not None:
        return closed
    if pid_alive(record.owner_pid):
        os.kill(record.owner_pid, signal.SIGKILL)
    latest = store.load(record.id) or record
    _end_group(latest.child_pid, cancellation.CANCEL_GRACE_SECONDS)
    return store.update(record.id, status=STOPPED, finished_at=time.time(), last_signal="stopped", last_signal_at=time.time()) or record


@dataclass(frozen=True)
class Cleaned:
    """A removed record and, when its worktree holds changes, where that worktree was left."""

    run_id:        str
    kept_worktree: str


def _dispose_worktree(record: RunRecord) -> str:
    """Remove the run's worktree when it changed nothing; the path of one that did (or cannot be checked)."""
    if not record.worktree_path or not Path(record.worktree_path).is_dir():
        return ""
    made = Worktree(record.worktree_root, record.worktree_path, record.worktree_branch, record.worktree_start)
    try:
        if has_changes(made):
            return record.worktree_path
        remove_worktree(made)
    except WorktreeError:
        return record.worktree_path
    return ""


def clean_runs(days: float, store: RunStore | None = None, now: float | None = None) -> list[Cleaned]:
    """Remove the records of runs that ended at least *days* days ago; a worktree with changes is kept."""
    store   = store or RunStore()
    now     = time.time() if now is None else now
    cleaned = []
    for record in store.all():
        if store.effective_status(record, now) in FINISHED and record.finished_at is not None and now - record.finished_at >= days * 86400:
            kept = _dispose_worktree(record)
            store.remove(record)
            cleaned.append(Cleaned(record.id, kept))
    return cleaned


def main() -> None:
    """Entry point of the monitor process: ``python -m nerdvana_cli.cli.supervisor <run id>``."""
    if len(sys.argv) != 2:
        sys.exit("usage: python -m nerdvana_cli.cli.supervisor <run id>")
    sys.exit(asyncio.run(monitor(sys.argv[1])))


if __name__ == "__main__":
    main()
