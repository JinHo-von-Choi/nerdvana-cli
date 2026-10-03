"""Scheduled runs: job definitions, the lock that keeps a job from overlapping itself, and the daemon loop.

Author: 최진호
Date:   2026-10-03

A job is a prompt, a schedule (see ``cli.cron``), a working directory and limits. The daemon fires a
due job by starting ``nerdvana run`` in a new process with the job's cost ceiling and approval mode and
saves what the run reported under ``<data root>/schedule/runs/<job>/``: ``<stamp>.json`` holds the run
result and ``<stamp>.log`` what the process wrote to standard error.

A run that was due while the daemon was not running is not replayed: every job starts counting when the
daemon starts, and a window the daemon slept through for more than two minutes is cut down to the last two.
Two runs of one job never overlap, across the daemon and ``schedule run`` alike, because both take the
job's lock file first. The daemon also stops starting jobs once the day's recorded cost, plus the ceilings
of the runs still going, reaches its daily ceiling.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shlex
import signal
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, fields
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import IO, Any

import yaml  # type: ignore[import-untyped,unused-ignore]

from nerdvana_cli.cli.cron import Schedule, ScheduleError, parse_schedule
from nerdvana_cli.core.config import paths as core_paths

logger = logging.getLogger(__name__)

COMMAND_ENV = "NERDVANA_SCHEDULE_COMMAND"

APPROVAL_MODES             = ("plan", "default")
DEFAULT_APPROVAL_MODE      = "plan"
DEFAULT_JOB_MAX_COST_USD   = 1.0
DEFAULT_DAILY_MAX_COST_USD = 10.0
RUN_TIMEOUT_SECONDS        = 3600
CATCH_UP                   = timedelta(seconds=120)

_KILL_GRACE_SECONDS = 10
_LOCK_FRESH_SECONDS = 10
_STDOUT_KEEP_CHARS  = 20_000
_NAME               = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}")

# nerdvana run exit codes (cli.run_output) -> the status recorded for the run
_STATUS_BY_EXIT = {0: "success", 1: "failed", 2: "config_error", 3: "limit_reached"}


@dataclass(frozen=True)
class Job:
    """One scheduled prompt."""

    name:          str
    schedule:      str
    prompt:        str
    cwd:           str
    max_cost_usd:  float = DEFAULT_JOB_MAX_COST_USD
    approval_mode: str   = DEFAULT_APPROVAL_MODE
    created_at:    str   = ""

    def parsed(self) -> Schedule:
        """The parsed schedule expression."""
        return parse_schedule(self.schedule)


def validate_job(job: Job) -> Job:
    """Return *job* when every field is usable; ScheduleError names the first one that is not."""
    if not _NAME.fullmatch(job.name):
        raise ScheduleError(f"job name '{job.name}' must be 1-64 letters, digits, '.', '_' or '-', starting with a letter or digit")
    try:
        job.parsed()
    except ScheduleError as exc:
        raise ScheduleError(f"job '{job.name}': {exc}") from exc
    if not job.prompt.strip():
        raise ScheduleError(f"job '{job.name}': the prompt is empty")
    if not job.cwd:
        raise ScheduleError(f"job '{job.name}': the working directory is empty")
    if job.approval_mode not in APPROVAL_MODES:
        raise ScheduleError(f"job '{job.name}': approval mode must be one of {', '.join(APPROVAL_MODES)}")
    if job.max_cost_usd < 0:
        raise ScheduleError(f"job '{job.name}': the cost ceiling cannot be negative")
    return job


def _job_from_mapping(raw: Any, index: int, source: Path) -> Job:
    where = f"{source}: job {index + 1}"
    if not isinstance(raw, dict):
        raise ScheduleError(f"{where}: expected a mapping, got {type(raw).__name__}")
    unknown = sorted(set(raw) - {item.name for item in fields(Job)})
    if unknown:
        raise ScheduleError(f"{where}: unknown key '{unknown[0]}'")
    missing = [key for key in ("name", "schedule", "prompt", "cwd") if key not in raw]
    if missing:
        raise ScheduleError(f"{where}: missing '{missing[0]}'")
    for key, value in raw.items():
        wanted = (int, float) if key == "max_cost_usd" else str
        if isinstance(value, bool) or not isinstance(value, wanted):
            raise ScheduleError(f"{where}: '{key}' has the wrong type ({type(value).__name__})")
    try:
        return validate_job(Job(**{**raw, "max_cost_usd": float(raw.get("max_cost_usd", DEFAULT_JOB_MAX_COST_USD))}))
    except ScheduleError as exc:
        raise ScheduleError(f"{where}: {exc}") from exc


class JobStore:
    """The job definitions, kept in one YAML file under the data root."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or core_paths.schedule_jobs_path()

    def load(self) -> list[Job]:
        """Every job in the file; a missing file is an empty list and a bad one is a ScheduleError."""
        try:
            data = yaml.safe_load(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return []
        except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
            raise ScheduleError(f"{self.path}: {' '.join(str(exc).split())}") from exc
        if data is None:
            return []
        entries = data.get("jobs") if isinstance(data, dict) else None
        if not isinstance(entries, list):
            raise ScheduleError(f"{self.path}: expected a 'jobs' list at the top level")
        jobs = [_job_from_mapping(raw, index, self.path) for index, raw in enumerate(entries)]
        names = [job.name for job in jobs]
        for name in names:
            if names.count(name) > 1:
                raise ScheduleError(f"{self.path}: job name '{name}' appears twice")
        return jobs

    def _save(self, jobs: list[Job]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(self.path.name + ".tmp")
        temporary.write_text(yaml.safe_dump({"jobs": [asdict(job) for job in jobs]}, sort_keys=False, allow_unicode=True), encoding="utf-8")
        os.replace(temporary, self.path)

    def next_name(self) -> str:
        """The first of ``job-1``, ``job-2`` ... that no job uses."""
        taken = {job.name for job in self.load()}
        number = 1
        while f"job-{number}" in taken:
            number += 1
        return f"job-{number}"

    def add(self, job: Job) -> None:
        """Store *job*; ScheduleError when it is invalid or its name is taken."""
        validate_job(job)
        jobs = self.load()
        if any(existing.name == job.name for existing in jobs):
            raise ScheduleError(f"a job named '{job.name}' already exists")
        self._save([*jobs, job])

    def remove(self, name: str) -> None:
        """Delete the job called *name*; ScheduleError when there is none."""
        jobs = self.load()
        kept = [job for job in jobs if job.name != name]
        if len(kept) == len(jobs):
            raise ScheduleError(f"no job named '{name}'")
        self._save(kept)

    def get(self, name: str) -> Job:
        """The job called *name*; ScheduleError when there is none."""
        for job in self.load():
            if job.name == name:
                return job
        raise ScheduleError(f"no job named '{name}'")


class JobLock:
    """A lock file that names the process holding it, so a crashed holder does not block the job for good."""

    def __init__(self, path: Path) -> None:
        self.path  = path
        self._held = False

    def _holder_alive(self) -> bool:
        try:
            pid = int(self.path.read_text(encoding="utf-8").strip())
        except FileNotFoundError:
            return False
        except ValueError:
            # Being written by a claimant that is still starting.
            try:
                return time.time() - self.path.stat().st_mtime < _LOCK_FRESH_SECONDS
            except FileNotFoundError:
                return False
        except OSError:
            return True
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True

    def acquire(self) -> bool:
        """Take the lock; False when a live process holds it. A lock left by a dead process is taken over."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        for _attempt in range(2):
            try:
                descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except FileExistsError:
                if self._holder_alive():
                    return False
                self.path.unlink(missing_ok=True)
                continue
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(str(os.getpid()))
            self._held = True
            return True
        return False

    def hand_to(self, pid: int) -> None:
        """Record *pid* as the holder, so the lock outlives this process while the run it started goes on."""
        temporary = self.path.with_name(self.path.name + ".tmp")
        temporary.write_text(str(pid), encoding="utf-8")
        os.replace(temporary, self.path)

    def release(self) -> None:
        """Give the lock up; nothing when this object does not hold it."""
        if self._held:
            self.path.unlink(missing_ok=True)
            self._held = False


def scheduler_command() -> list[str]:
    """The command that starts nerdvana: ``$NERDVANA_SCHEDULE_COMMAND`` when set, else this interpreter's module."""
    override = os.environ.get(COMMAND_ENV, "").strip()
    return shlex.split(override) if override else [sys.executable, "-m", "nerdvana_cli.main"]


def build_run_command(job: Job) -> list[str]:
    """The ``nerdvana run`` command line for *job*; a cost ceiling also requires a known price."""
    command = [*scheduler_command(), "run", "--cwd", job.cwd, "--output-format", "json", "--approval-mode", job.approval_mode]
    if job.max_cost_usd > 0:
        command += ["--max-cost-usd", f"{job.max_cost_usd:g}", "--require-price"]
    return [*command, "--", job.prompt]


class LaunchError(OSError):
    """The run's process could not be started."""


@dataclass(frozen=True)
class RunRecord:
    """What is saved about one run, or one run that was skipped."""

    job:         str
    status:      str
    exit_code:   int | None
    started_at:  str
    finished_at: str
    cost_usd:    float
    command:     list[str]
    result:      Any
    note:        str
    record_path: str
    log_path:    str


def _stamp(when: datetime) -> str:
    return when.strftime("%Y%m%dT%H%M%S%f")


def _save_record(job: Job, started: datetime, finished: datetime, **values: Any) -> RunRecord:
    """Write the record of a run to ``runs/<job>/<stamp>.json`` and return it."""
    directory = core_paths.schedule_runs_dir() / job.name
    directory.mkdir(parents=True, exist_ok=True)
    stamp  = _stamp(started)
    record = RunRecord(
        job=job.name, started_at=started.isoformat(timespec="seconds"), finished_at=finished.isoformat(timespec="seconds"),
        record_path=str(directory / f"{stamp}.json"),
        **{"exit_code": None, "cost_usd": 0.0, "command": [], "result": None, "note": "", "log_path": "", **values},
    )
    temporary = directory / f"{stamp}.json.tmp"
    temporary.write_text(json.dumps(asdict(record), ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, record.record_path)
    return record


def record_skipped(job: Job, when: datetime, status: str, note: str) -> RunRecord:
    """Record that *job* was due at *when* and did not run (``skipped_overlap``, ``skipped_budget`` or ``error``)."""
    return _save_record(job, when, when, status=status, note=note)


def spent_on(day: date) -> float:
    """The cost recorded for runs that started on *day*."""
    total = 0.0
    for path in core_paths.schedule_runs_dir().glob(f"*/{day:%Y%m%d}T*.json"):
        try:
            total += float(json.loads(path.read_text(encoding="utf-8"))["cost_usd"])
        except (OSError, ValueError, KeyError, TypeError) as exc:
            logger.warning("run record %s cannot be read for the daily cost: %s", path, exc)
    return total


def latest_record(name: str) -> dict[str, Any] | None:
    """The newest run record of the job *name*, as saved; None when it never ran."""
    files = sorted((core_paths.schedule_runs_dir() / name).glob("*.json"))
    if not files:
        return None
    try:
        data = json.loads(files[-1].read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


@dataclass
class RunningJob:
    """A run in progress: its process, the files it writes to and the lock that guards it."""

    job:      Job
    process:  subprocess.Popen[bytes]
    started:  datetime
    deadline: float
    lock:     JobLock
    command:  list[str]
    out_path: Path
    log_path: Path
    streams:  tuple[IO[bytes], IO[bytes]]


def start_job(job: Job, now: datetime) -> RunningJob | None:
    """Start the run of *job*; None when a run of it is already going, LaunchError when the process cannot start."""
    lock = JobLock(core_paths.schedule_lock_path(job.name))
    if not lock.acquire():
        return None
    directory = core_paths.schedule_runs_dir() / job.name
    directory.mkdir(parents=True, exist_ok=True)
    out_path = directory / f"{_stamp(now)}.out"
    log_path = directory / f"{_stamp(now)}.log"
    command  = build_run_command(job)
    streams  = (out_path.open("wb"), log_path.open("wb"))
    try:
        process = subprocess.Popen(
            command, cwd=job.cwd, stdin=subprocess.DEVNULL, stdout=streams[0], stderr=streams[1], start_new_session=True,
        )
    except OSError as exc:
        for stream in streams:
            stream.close()
        out_path.unlink(missing_ok=True)
        lock.release()
        raise LaunchError(f"cannot start {command[0]}: {exc}") from exc
    lock.hand_to(process.pid)
    return RunningJob(job, process, now, time.monotonic() + RUN_TIMEOUT_SECONDS, lock, command, out_path, log_path, streams)


def _terminate(running: RunningJob) -> None:
    """Stop the run's whole process group: ask first, insist after a grace period."""
    for sent in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(running.process.pid, sent)
        except ProcessLookupError:
            return
        try:
            running.process.wait(timeout=_KILL_GRACE_SECONDS)
            return
        except subprocess.TimeoutExpired:
            continue


def _read_result(running: RunningJob) -> tuple[Any, float]:
    """The run's result object and the cost it reports; the raw text when standard output is not JSON."""
    text = running.out_path.read_text(encoding="utf-8", errors="replace")
    running.out_path.unlink(missing_ok=True)
    if not text.strip():
        return None, 0.0
    try:
        result = json.loads(text)
    except ValueError:
        return {"stdout": text[:_STDOUT_KEEP_CHARS]}, 0.0
    cost = result.get("total_cost_usd") if isinstance(result, dict) else None
    return result, float(cost) if isinstance(cost, (int, float)) and not isinstance(cost, bool) else 0.0


def finish_job(running: RunningJob, status: str = "") -> RunRecord:
    """Save the record of a run whose process has ended, release its lock and return the record."""
    for stream in running.streams:
        stream.close()
    code = running.process.returncode
    try:
        result, cost = _read_result(running)
        return _save_record(
            running.job, running.started, datetime.now(), status=status or _STATUS_BY_EXIT.get(code, "failed"),
            exit_code=code, cost_usd=cost, command=running.command, result=result, log_path=str(running.log_path),
        )
    finally:
        running.lock.release()


def poll_job(running: RunningJob) -> RunRecord | None:
    """None while the run goes on; its record once it ended, or once it passed the time limit and was stopped."""
    if running.process.poll() is not None:
        return finish_job(running)
    if time.monotonic() >= running.deadline:
        _terminate(running)
        return finish_job(running, "timeout")
    return None


def run_job_now(job: Job, poll_seconds: float = 0.2) -> RunRecord | None:
    """Run *job* once and wait for it; None when a run of it is already going."""
    running = start_job(job, datetime.now())
    if running is None:
        return None
    while True:
        record = poll_job(running)
        if record is not None:
            return record
        time.sleep(poll_seconds)


class Scheduler:
    """Decides on every tick which jobs are due, starts them and collects the ones that finished."""

    def __init__(self, store: JobStore, daily_cap_usd: float = DEFAULT_DAILY_MAX_COST_USD) -> None:
        self.store         = store
        self.daily_cap_usd = daily_cap_usd
        self.running:      dict[str, RunningJob] = {}
        self._anchors:     dict[str, datetime]   = {}
        self._jobs:        list[Job]             = []

    def reap(self) -> list[str]:
        """Collect the runs that finished; returns one log line for each."""
        messages: list[str] = []
        for name in list(self.running):
            record = poll_job(self.running[name])
            if record is not None:
                del self.running[name]
                messages.append(f"finished {name}: {record.status}, cost ${record.cost_usd:.4f}, record {record.record_path}")
        return messages

    def _fire(self, job: Job, now: datetime) -> str:
        committed = spent_on(now.date()) + sum(item.job.max_cost_usd for item in self.running.values())
        if self.daily_cap_usd > 0 and committed >= self.daily_cap_usd:
            record_skipped(job, now, "skipped_budget", f"daily ceiling ${self.daily_cap_usd:g} reached")
            return f"skipped {job.name}: the daily cost ceiling of ${self.daily_cap_usd:g} is reached"
        try:
            running = start_job(job, now)
        except LaunchError as exc:
            record_skipped(job, now, "error", str(exc))
            return f"failed to start {job.name}: {exc}"
        if running is None:
            record_skipped(job, now, "skipped_overlap", "the previous run is still going")
            return f"skipped {job.name}: the previous run is still going"
        self.running[job.name] = running
        return f"started {job.name}"

    def _current_jobs(self) -> tuple[list[Job], list[str]]:
        try:
            self._jobs = self.store.load()
        except ScheduleError as exc:
            return self._jobs, [f"cannot read the job file, keeping the last list: {exc}"]
        return self._jobs, []

    def tick(self, now: datetime) -> list[str]:
        """Collect finished runs, reload the job file and start every job that came due; returns log lines."""
        messages        = self.reap()
        jobs, problems  = self._current_jobs()
        messages.extend(problems)
        for gone in set(self._anchors) - {job.name for job in jobs}:
            del self._anchors[gone]
        for job in jobs:
            schedule = job.parsed()
            since    = self._anchors.setdefault(job.name, now)
            due      = schedule.due(since, now, CATCH_UP)
            self._anchors[job.name] = schedule.rearm(since, now, due)
            if due:
                messages.append(self._fire(job, now))
        return messages

    def shutdown(self) -> list[str]:
        """Stop every run still going and record it as interrupted."""
        messages: list[str] = []
        for name, running in list(self.running.items()):
            _terminate(running)
            finish_job(running, "interrupted")
            del self.running[name]
            messages.append(f"stopped {name}: interrupted")
        return messages


def serve(
    scheduler:    Scheduler,
    tick_seconds: float,
    emit:         Callable[[str], None],
    clock:        Callable[[], datetime] = datetime.now,
    sleep:        Callable[[float], None] = time.sleep,
    should_stop:  Callable[[], bool] = lambda: False,
) -> None:
    """Tick until *should_stop* says so or the process is interrupted; runs still going are stopped on the way out."""
    try:
        while not should_stop():
            for line in scheduler.tick(clock()):
                emit(f"{clock():%Y-%m-%d %H:%M:%S} {line}")
            sleep(tick_seconds)
    finally:
        for line in scheduler.shutdown():
            emit(f"{clock():%Y-%m-%d %H:%M:%S} {line}")


def systemd_unit() -> str:
    """A systemd user unit that runs the daemon; printed for the user to review and install."""
    command = " ".join(shlex.quote(part) for part in [*scheduler_command(), "schedule", "daemon"])
    return (
        "# Save as ~/.config/systemd/user/nerdvana-schedule.service, then run:\n"
        "#   systemctl --user daemon-reload\n"
        "#   systemctl --user enable --now nerdvana-schedule.service\n"
        "[Unit]\n"
        "Description=NerdVana scheduled runs\n"
        "After=network-online.target\n"
        "\n"
        "[Service]\n"
        "Type=simple\n"
        f"ExecStart={command}\n"
        "Restart=on-failure\n"
        "RestartSec=30\n"
        "\n"
        "[Install]\n"
        "WantedBy=default.target\n"
    )
