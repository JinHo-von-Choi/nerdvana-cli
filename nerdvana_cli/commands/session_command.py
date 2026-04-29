"""session sub-app — session transcript management.

Commands:
  session list    — list *.jsonl transcripts sorted by mtime desc
  session resume  — resume a session transcript by ID
  session purge   — delete transcripts older than a given age

Author: 최진호
Date:   2026-04-29
"""

from __future__ import annotations

import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import typer
from rich.console import Console

from nerdvana_cli.core import paths as core_paths

console = Console()

session_app = typer.Typer(
    name           = "session",
    help           = "Session transcript management.",
    add_completion = False,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sessions_dir() -> Path:
    return core_paths.user_sessions_dir()


def _list_session_files(sessions_dir: Path) -> list[Path]:
    """Return *.jsonl files sorted by mtime descending."""
    if not sessions_dir.is_dir():
        return []
    files = list(sessions_dir.glob("*.jsonl"))
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return files


def _first_message(path: Path) -> str:
    """Return the first human message text from a JSONL transcript."""
    try:
        with open(path, encoding="utf-8") as fp:
            for line in fp:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                role    = obj.get("role", "")
                content = obj.get("content", "")
                if role == "user":
                    if isinstance(content, str):
                        return content[:80]
                    if isinstance(content, list):
                        for block in content:
                            if isinstance(block, dict) and block.get("type") == "text":
                                return str(block.get("text", ""))[:80]
    except OSError:
        pass
    return "(no preview)"


def _message_count(path: Path) -> int:
    """Count non-empty JSONL lines (proxy for message count)."""
    try:
        with open(path, encoding="utf-8") as fp:
            return sum(1 for line in fp if line.strip())
    except OSError:
        return 0


def _parse_duration(spec: str) -> int | None:
    """Parse age spec like '7d', '30d', 'all' into seconds.

    Returns None for 'all'.
    Raises typer.BadParameter for unrecognised patterns.
    """
    if spec.lower() == "all":
        return None
    m = re.fullmatch(r"(\d+)([dh])", spec.lower())
    if not m:
        raise typer.BadParameter(
            f"Unrecognised age spec {spec!r}. Use '7d', '30d', '24h', or 'all'."
        )
    value = int(m.group(1))
    unit  = m.group(2)
    return value * 86400 if unit == "d" else value * 3600


def _build_session_record(path: Path) -> dict[str, Any]:
    stat    = path.stat()
    mtime   = stat.st_mtime
    return {
        "id":       path.stem,
        "preview":  _first_message(path),
        "messages": _message_count(path),
        "mtime":    mtime,
        "mtime_iso": datetime.fromtimestamp(mtime, tz=UTC).isoformat(),
    }


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

@session_app.command("list")
def session_list(
    limit:       int  = typer.Option(20, "--limit",  help="Maximum sessions to display."),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON."),
) -> None:
    """List session transcripts sorted by most-recent first."""
    sessions_dir = _sessions_dir()
    files        = _list_session_files(sessions_dir)[:limit]

    if json_output:
        records = [_build_session_record(f) for f in files]
        console.print(json.dumps(records, indent=2))
        return

    if not files:
        console.print("[dim]No sessions found.[/dim]")
        return

    console.print(f"[bold]Sessions ({len(files)})[/bold]")
    for path in files:
        rec  = _build_session_record(path)
        dt   = datetime.fromtimestamp(rec["mtime"]).strftime("%Y-%m-%d %H:%M")
        msgs = rec["messages"]
        sid  = rec["id"]
        prev = rec["preview"]
        console.print(f"  [cyan]{sid}[/cyan]  {dt}  [{msgs} lines]  {prev}")


@session_app.command("resume")
def session_resume(session_id: str = typer.Argument(..., help="Session ID to resume.")) -> None:
    """Resume a session by setting NERDVANA_RESUME and launching the REPL."""
    sessions_dir = _sessions_dir()
    target       = sessions_dir / f"{session_id}.jsonl"
    if not target.exists():
        console.print(f"[red]Session '{session_id}' not found.[/red]")
        raise typer.Exit(1)

    os.environ["NERDVANA_RESUME"] = session_id
    console.print(f"[dim]Resuming session {session_id}…[/dim]")
    import asyncio

    from nerdvana_cli.main import repl_loop

    asyncio.run(repl_loop())


@session_app.command("purge")
def session_purge(
    older_than: str  = typer.Option("30d", "--older-than", help="Age threshold, e.g. 30d, 7d, all."),
    dry_run:    bool = typer.Option(False, "--dry-run", help="Show files that would be deleted."),
) -> None:
    """Delete session transcripts older than the given threshold."""
    import time

    sessions_dir = _sessions_dir()
    files        = _list_session_files(sessions_dir)

    if not files:
        console.print("[dim]No sessions to purge.[/dim]")
        return

    max_age_s = _parse_duration(older_than)
    now_ts    = time.time()

    targets = [
        f for f in files
        if max_age_s is None or (now_ts - f.stat().st_mtime) > max_age_s
    ]

    if not targets:
        console.print("[dim]No sessions matched the criteria.[/dim]")
        return

    if dry_run:
        console.print(f"[dim]Dry run — {len(targets)} file(s) would be deleted:[/dim]")
        for f in targets:
            console.print(f"  {f.name}")
        return

    for f in targets:
        f.unlink(missing_ok=True)
    console.print(f"Purged {len(targets)} session(s).")
