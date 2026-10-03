"""`nerdvana agents ...`: run agents in the background under a supervisor and keep track of them.

Author: 최진호
Date:   2026-10-03

    nerdvana agents start "<prompt>" [--worktree] [--max-cost-usd X] [--key K] [--cwd DIR] [--approval-mode M]
    nerdvana agents list | show ID | attach ID | stop ID | resume ID | clean [--days N]

The behavior behind these commands is in ``cli.supervisor`` and ``core.state.run_store``; see docs/background.md.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import NoReturn

import typer
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from nerdvana_cli.cli.supervisor import SupervisorError, clean_runs, resume_run, start_run, stop_run
from nerdvana_cli.core.state.run_store import FINISHED, ORPHANED, RUNNING, RunRecord, RunStore, read_lines

console     = Console()
err_console = Console(stderr=True)

agents_app = typer.Typer(
    name           = "agents",
    help           = "Run agents in the background, with a record that survives a restart.",
    add_completion = False,
)

DEFAULT_CLEAN_DAYS = 7.0
_FOLLOW_SECONDS    = 0.3


def _fail(message: str, code: int = 1) -> NoReturn:
    err_console.print(f"[red]Error: {escape(message)}[/red]")
    raise typer.Exit(code)


def format_age(seconds: float) -> str:
    """A short age: ``45s``, ``12m``, ``3h`` or ``2d``."""
    seconds = max(0.0, seconds)
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60)):
        if seconds >= size:
            return f"{int(seconds // size)}{unit}"
    return f"{int(seconds)}s"


def render_event(line: str) -> str:
    """One stream-json line of a run as a line of text; empty for events not worth showing."""
    try:
        event = json.loads(line)
    except ValueError:
        return line
    kind = event.get("type") if isinstance(event, dict) else None
    if kind == "text":
        return str(event.get("text", ""))
    if kind == "tool_start":
        return f"> {event.get('name', '')} {event.get('summary', '')}".rstrip()
    if kind == "notice":
        return str(event.get("text", ""))
    if kind == "result":
        return f"[result: {event.get('subtype', '')}, cost ${event.get('total_cost_usd', 0)}]"
    return ""


def _find(store: RunStore, run_id: str) -> RunRecord:
    record = store.resolve(run_id)
    if record is None:
        _fail(f"no run '{run_id}' (or the prefix is ambiguous); see: nerdvana agents list")
    return record


def _status_of(store: RunStore, record: RunRecord) -> str:
    return store.effective_status(record)


@agents_app.command("start")
def agents_start(
    prompt:        str   = typer.Argument(..., help="The task for the agent"),
    worktree:      bool  = typer.Option(False, "--worktree", help="Work in a git worktree of its own, on a new branch"),
    max_cost_usd:  float = typer.Option(0.0, "--max-cost-usd", help="Stop the run once its estimated cost reaches this many USD (0 = no ceiling)"),
    key:           str   = typer.Option("", "--key", help="Idempotency key: starting again with the same key returns the existing run"),
    cwd:           str   = typer.Option("", "--cwd", help="Directory to work in (default: the current directory)"),
    approval_mode: str   = typer.Option("", "--approval-mode", help="default | auto_edit | yolo | plan (default: from the configuration; a call that needs approval is refused)"),
) -> None:
    """Start a run in the background and print its id."""
    directory = Path(cwd or os.getcwd()).expanduser().resolve()
    if not directory.is_dir():
        _fail(f"{directory} is not a directory", 2)
    try:
        record, started = start_run(prompt, str(directory), worktree, max_cost_usd, key, approval_mode)
    except SupervisorError as exc:
        _fail(str(exc))
    console.print(f"{'Started' if started else 'Already started'} run [bold]{record.id}[/bold]" + (f" in worktree {escape(record.worktree_path)}" if record.worktree_path else ""))
    console.print(f"[dim]nerdvana agents attach {record.id} follows it; nerdvana agents show {record.id} shows the result.[/dim]")


@agents_app.command("list")
def agents_list() -> None:
    """List the runs: id, status, age, cost so far and the last sign of life."""
    store   = RunStore()
    records = store.all()
    if not records:
        console.print("No runs.")
        return
    now   = time.time()
    table = Table("Id", "Kind", "Status", "Age", "Cost", "Last signal")
    for record in records:
        status = _status_of(store, record)
        signal = f"{record.last_signal} ({format_age(now - record.last_signal_at)} ago)" if record.last_signal else ""
        table.add_row(record.id, record.kind, status, format_age(now - record.started_at), f"${record.cost_usd:.4f}", escape(signal))
    console.print(table)
    if any(_status_of(store, record) == ORPHANED for record in records):
        console.print("[dim]An orphaned run lost its process; nerdvana agents resume <id> continues it from its session transcript.[/dim]")


def _tail(path: Path, count: int) -> list[str]:
    rendered = [render_event(line) for line in read_lines(path)[0]]
    return [line for line in rendered if line][-count:]


@agents_app.command("show")
def agents_show(
    run_id: str = typer.Argument(..., help="Id of the run (a unique prefix is enough)"),
    lines:  int = typer.Option(20, "--lines", min=1, help="How many lines of the log to show"),
) -> None:
    """Show a run: its record, the tail of its log and its result."""
    store  = RunStore()
    record = _find(store, run_id)
    status = _status_of(store, record)
    console.print(f"[bold]{record.id}[/bold]  {status}  ({record.kind}, pid {record.owner_pid}, session {escape(record.session_id or '-')})")
    console.print(f"prompt: {escape(record.prompt[:200])}")
    console.print(f"cwd: {escape(record.cwd)}" + (f"  worktree: {escape(record.worktree_path)} ({escape(record.worktree_branch)})" if record.worktree_path else ""))
    if record.error:
        console.print(f"[red]error: {escape(record.error)}[/red]")
    if record.log_path:
        console.print(f"[dim]--- log: last {lines} lines[/dim]")
        for line in _tail(Path(record.log_path), lines):
            console.print(escape(line), highlight=False)
    if record.result_path and Path(record.result_path).is_file():
        console.print(f"[dim]--- result: {escape(record.result_path)}[/dim]")
        console.print(escape(Path(record.result_path).read_text(encoding="utf-8")), highlight=False)


@agents_app.command("attach")
def agents_attach(run_id: str = typer.Argument(..., help="Id of the run (a unique prefix is enough)")) -> None:
    """Follow the log of a run until it ends; Ctrl+C only stops following."""
    store  = RunStore()
    record = _find(store, run_id)
    if not record.log_path:
        _fail(f"run {record.id} has no log; nerdvana agents show {record.id} shows its record")
    path, offset = Path(record.log_path), 0
    try:
        while True:
            current = store.load(record.id) or record
            lines, offset = read_lines(path, offset)
            for text in filter(None, map(render_event, lines)):
                console.print(escape(text), highlight=False)
            if store.effective_status(current) != RUNNING:
                break
            time.sleep(_FOLLOW_SECONDS)
    except KeyboardInterrupt:
        console.print(f"\n[dim]Detached; run {record.id} goes on.[/dim]")
        return
    console.print(f"[dim]Run {record.id} {store.effective_status(current)}.[/dim]")


@agents_app.command("stop")
def agents_stop(run_id: str = typer.Argument(..., help="Id of the run (a unique prefix is enough)")) -> None:
    """Stop a run: ask it to end, then kill it when it does not."""
    try:
        record = stop_run(run_id)
    except SupervisorError as exc:
        _fail(str(exc))
    console.print(f"Run {record.id} {record.status}.")


@agents_app.command("resume")
def agents_resume(run_id: str = typer.Argument(..., help="Id of the run (a unique prefix is enough)")) -> None:
    """Start an orphaned, stopped or failed run again from its session transcript."""
    try:
        record = resume_run(run_id)
    except SupervisorError as exc:
        _fail(str(exc))
    console.print(f"Resumed run [bold]{record.id}[/bold] (attempt {record.attempts}) from session {escape(record.resume_session)}")


@agents_app.command("clean")
def agents_clean(days: float = typer.Option(DEFAULT_CLEAN_DAYS, "--days", min=0.0, help="Remove runs that ended at least this many days ago")) -> None:
    """Remove the records of finished runs; a worktree with changes is kept and its path printed."""
    cleaned = clean_runs(days)
    console.print(f"Removed {len(cleaned)} run(s) that ended {days:g} or more days ago.")
    for item in cleaned:
        if item.kept_worktree:
            console.print(f"Kept the worktree of {item.run_id} (it has changes): {escape(item.kept_worktree)}")


__all__ = ["FINISHED", "agents_app", "format_age", "render_event"]
