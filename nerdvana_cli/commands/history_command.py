"""nerdvana history search and /history: search of past session transcripts.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

import os
import shlex
from datetime import datetime
from typing import TYPE_CHECKING

import typer
from rich.markup import escape

from nerdvana_cli.cli.history_search import INDEX_FILENAME, Hit, search_history
from nerdvana_cli.commands.cost_command import parse_since
from nerdvana_cli.core import paths

if TYPE_CHECKING:
    from nerdvana_cli.ui.app import NerdvanaApp

DEFAULT_LIMIT = 20
USAGE         = "Usage: /history <query> [--since 7d] [--cwd DIR]"

history_app = typer.Typer(name="history", help="Search past session transcripts.", add_completion=False)


def format_hit(hit: Hit) -> str:
    """One line per hit: session id, date, role and the text around the match."""
    when = datetime.fromisoformat(hit.stamp).strftime("%Y-%m-%d %H:%M") if hit.stamp else "unknown"
    return f"{hit.session_id}  {when}  {hit.role:<9}  {hit.snippet}"


def run_search(query: str, since: str, cwd: str | None, limit: int = DEFAULT_LIMIT) -> list[str]:
    """The result lines of a search of the stored transcripts; ValueError for a malformed *since*."""
    cutoff = parse_since(since)
    wanted = os.path.realpath(cwd) if cwd else None
    hits   = search_history(
        query, paths.user_sessions_dir(), since=cutoff, cwd=wanted, limit=limit, index_path=paths.user_data_home() / INDEX_FILENAME,
    )
    return [format_hit(hit) for hit in hits] or ["No matches."]


@history_app.command("search")
def history_search(
    query: str = typer.Argument(..., help="Words to look for; every word must be in the message."),
    since: str = typer.Option("all", "--since", help="Only messages from this window (e.g. 7d, 24h, all)."),
    cwd:   str = typer.Option("", "--cwd", help="Only sessions started in this directory or below it (e.g. .)."),
    limit: int = typer.Option(DEFAULT_LIMIT, "--limit", help="Most recent messages to show."),
) -> None:
    """Search stored session transcripts; prints session id, date, role and a snippet per message."""
    try:
        lines = run_search(query, since, cwd or None, limit)
    except ValueError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(2) from exc
    typer.echo("\n".join(lines))


def _parse_slash(args: str) -> tuple[str, str, str | None]:
    """(query, since, cwd) from the text after ``/history``; ValueError when it is malformed."""
    tokens = shlex.split(args)
    since, cwd, words = "all", None, []
    while tokens:
        token = tokens.pop(0)
        if token in ("--since", "--cwd"):
            if not tokens:
                raise ValueError(f"{token} needs a value")
            value = tokens.pop(0)
            since, cwd = (value, cwd) if token == "--since" else (since, value)
        else:
            words.append(token)
    return " ".join(words), since, cwd


async def handle_history(app: NerdvanaApp, args: str) -> None:
    """Handle /history <query> [--since 7d] [--cwd DIR]."""
    try:
        query, since, cwd = _parse_slash(args)
        if not query:
            raise ValueError(USAGE)
        text = "\n".join(run_search(query, since, cwd))
    except ValueError as exc:
        app._add_chat_message(f"[red]{escape(str(exc))}[/red]")
        return
    app._add_chat_message(escape(text), raw_text=text)
