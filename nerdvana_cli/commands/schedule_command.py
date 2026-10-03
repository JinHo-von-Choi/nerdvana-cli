"""`nerdvana schedule ...`: run prompts on a cron schedule or at an interval, with a local daemon.

Author: 최진호
Date:   2026-10-03

    nerdvana schedule add "0 3 * * *" --prompt "..." [--cwd DIR] [--max-cost-usd X] [--approval-mode plan|default] [--name N]
    nerdvana schedule list | remove NAME | run NAME | daemon | install-systemd

The behavior behind these commands is in ``core.scheduler``; see docs/scheduling.md.
"""

from __future__ import annotations

import os
import signal
from datetime import datetime
from pathlib import Path
from typing import NoReturn

import typer
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from nerdvana_cli.core.cron import ScheduleError
from nerdvana_cli.core.scheduler import (
    DEFAULT_APPROVAL_MODE,
    DEFAULT_DAILY_MAX_COST_USD,
    DEFAULT_JOB_MAX_COST_USD,
    Job,
    JobStore,
    LaunchError,
    Scheduler,
    latest_record,
    run_job_now,
    serve,
    systemd_unit,
)

console     = Console()
err_console = Console(stderr=True)

schedule_app = typer.Typer(
    name           = "schedule",
    help           = "Run prompts on a schedule with a local daemon.",
    add_completion = False,
)


def _fail(message: str, code: int = 2) -> NoReturn:
    err_console.print(f"[red]Error: {escape(message)}[/red]")
    raise typer.Exit(code)


def _stop(_signum: int, _frame: object) -> NoReturn:
    raise KeyboardInterrupt


@schedule_app.command("add")
def schedule_add(
    when:          str   = typer.Argument(..., metavar="SCHEDULE", help='Five-field cron expression ("0 3 * * *") or an interval ("every 15m")'),
    prompt:        str   = typer.Option(..., "--prompt", help="The prompt each run gets"),
    cwd:           str   = typer.Option("", "--cwd", help="Directory the run works in (default: the current directory)"),
    max_cost_usd:  float = typer.Option(DEFAULT_JOB_MAX_COST_USD, "--max-cost-usd", help="Cost ceiling of one run in USD (0 = no ceiling)"),
    approval_mode: str   = typer.Option(DEFAULT_APPROVAL_MODE, "--approval-mode", help="plan (read-only, default) or default"),
    name:          str   = typer.Option("", "--name", help="Job name (default: job-1, job-2, ...)"),
) -> None:
    """Add a scheduled job."""
    directory = Path(cwd or os.getcwd()).expanduser().resolve()
    if not directory.is_dir():
        _fail(f"{directory} is not a directory")
    store = JobStore()
    try:
        job = Job(name or store.next_name(), when, prompt, str(directory), max_cost_usd, approval_mode, datetime.now().isoformat(timespec="seconds"))
        store.add(job)
        following = job.parsed().next_after(datetime.now())
    except ScheduleError as exc:
        _fail(str(exc))
    console.print(f"Added job [bold]{escape(job.name)}[/bold]; next run at {following:%Y-%m-%d %H:%M}")
    console.print("[dim]Runs only while `nerdvana schedule daemon` is running.[/dim]")


@schedule_app.command("list")
def schedule_list() -> None:
    """List the scheduled jobs with their next run and the outcome of the last one."""
    try:
        jobs = JobStore().load()
    except ScheduleError as exc:
        _fail(str(exc))
    if not jobs:
        console.print("No scheduled jobs.")
        return
    table = Table("Name", "Schedule", "Next run", "Mode", "Max cost", "Last run")
    now   = datetime.now()
    for job in jobs:
        last = latest_record(job.name)
        table.add_row(
            escape(job.name), escape(job.schedule), f"{job.parsed().next_after(now):%Y-%m-%d %H:%M}", job.approval_mode,
            f"${job.max_cost_usd:g}" if job.max_cost_usd else "none",
            f"{last['status']} at {last['started_at']}" if last else "never",
        )
    console.print(table)


@schedule_app.command("remove")
def schedule_remove(name: str = typer.Argument(..., help="Name of the job")) -> None:
    """Remove a scheduled job (its saved run records stay)."""
    try:
        JobStore().remove(name)
    except ScheduleError as exc:
        _fail(str(exc))
    console.print(f"Removed job {escape(name)}")


@schedule_app.command("run")
def schedule_run(name: str = typer.Argument(..., help="Name of the job")) -> None:
    """Run a job once now and wait for it; the exit code is the run's."""
    try:
        job = JobStore().get(name)
    except ScheduleError as exc:
        _fail(str(exc))
    try:
        record = run_job_now(job)
    except LaunchError as exc:
        _fail(str(exc), 1)
    if record is None:
        _fail(f"a run of '{name}' is already going", 1)
    result = record.result.get("result") if isinstance(record.result, dict) else None
    if result:
        console.print(escape(str(result)))
    console.print(f"[dim]{record.status}, cost ${record.cost_usd:.4f}, record {escape(record.record_path)}[/dim]")
    if record.status != "success":
        raise typer.Exit(record.exit_code or 1)


@schedule_app.command("daemon")
def schedule_daemon(
    max_daily_cost_usd: float = typer.Option(DEFAULT_DAILY_MAX_COST_USD, "--max-daily-cost-usd", help="Stop starting jobs once a day's recorded cost reaches this many USD (0 = no ceiling)"),
    tick_seconds:       float = typer.Option(15.0, "--tick-seconds", min=1.0, help="Seconds between checks for due jobs"),
) -> None:
    """Run in the foreground and start each job when it is due. Runs missed while the daemon was down are not replayed."""
    store = JobStore()
    try:
        count = len(store.load())
    except ScheduleError as exc:
        _fail(str(exc))
    signal.signal(signal.SIGTERM, _stop)
    typer.echo(f"{datetime.now():%Y-%m-%d %H:%M:%S} scheduler started with {count} job(s); daily cost ceiling ${max_daily_cost_usd:g}")
    try:
        serve(Scheduler(store, max_daily_cost_usd), tick_seconds, emit=lambda line: typer.echo(line))
    except KeyboardInterrupt:
        typer.echo("scheduler stopped")


@schedule_app.command("install-systemd")
def schedule_install_systemd() -> None:
    """Print a systemd user unit for the daemon; nothing is installed."""
    typer.echo(systemd_unit(), nl=False)

