"""memory sub-app — project memory management.

Commands:
  memory list    — list memories by scope
  memory add     — write a new memory
  memory remove  — delete a memory by name
  memory purge   — delete all memories in a scope
  memory inbox   : list the agent's proposed memories with a diff (memory.review)
  memory approve : apply one proposal, or every pending one with --all
  memory reject  : drop a proposal
  memory forget  : delete a memory after confirmation
  memory stale   : list memories not modified for N days and never read

Storage: core/memories.py MemoriesManager — same helper that /memories
slash command (memory_commands.py:handle_memories) uses.

Author: 최진호
Date:   2026-04-29
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Callable

import typer
from rich.console import Console
from rich.markup import escape

from nerdvana_cli.commands import memory_review_text as review
from nerdvana_cli.core.memories import MemoriesManager, MemoryScope
from nerdvana_cli.core.memory_index import MemorySource

console = Console()

memory_app = typer.Typer(
    name           = "memory",
    help           = "Project memory management.",
    add_completion = False,
)

_SCOPE_CHOICES = ("project", "global", "rule")
_SCOPE_MAP: dict[str, MemoryScope] = {
    "project": MemoryScope.PROJECT_KNOWLEDGE,
    "global":  MemoryScope.USER_GLOBAL,
    "rule":    MemoryScope.PROJECT_RULE,
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _resolve_scope(scope: str) -> MemoryScope:
    mapped = _SCOPE_MAP.get(scope.lower())
    if mapped is None:
        valid = ", ".join(_SCOPE_CHOICES)
        raise typer.BadParameter(f"Unknown scope '{scope}'. Valid: {valid}.")
    return mapped


def _cwd() -> str:
    return os.getcwd()


def _show(produce: Callable[[], str]) -> None:
    """Print the text *produce* returns; a ReviewError becomes a red message and exit status 1."""
    try:
        console.print(produce())
    except review.ReviewError as exc:
        console.print(f"[red]{escape(str(exc))}[/red]")
        raise typer.Exit(1) from None


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

@memory_app.command("list")
def memory_list(
    scope: str = typer.Option("project", "--scope", help="Scope: project | global | rule."),
) -> None:
    """List memories in the given scope."""
    import datetime

    resolved = _resolve_scope(scope)
    mgr      = MemoriesManager(_cwd())

    # PROJECT_RULE is NIRNA.md — direct file listing not meaningful via list_memories
    if resolved == MemoryScope.PROJECT_RULE:
        from nerdvana_cli.core import paths as core_paths

        nirnamd = core_paths.project_nirnamd_path(_cwd())
        if nirnamd.exists():
            console.print(f"[bold]PROJECT_RULE[/bold] → {nirnamd}")
        else:
            console.print("[dim]NIRNA.md not found in current directory.[/dim]")
        return

    entries = mgr.list_memories()
    # Filter by scope
    entries = [e for e in entries if e.scope == resolved]

    if not entries:
        console.print(f"[dim]No memories in scope '{scope}'.[/dim]")
        return

    console.print(f"[bold]{resolved} memories ({len(entries)})[/bold]")
    for e in entries:
        created  = datetime.datetime.fromtimestamp(e.created).strftime("%Y-%m-%d")
        modified = datetime.datetime.fromtimestamp(e.mtime).strftime("%Y-%m-%d")
        console.print(f"  [cyan]{e.name}[/cyan]  {e.size:>6}B  created {created}  modified {modified}  {e.source}")


@memory_app.command("add")
def memory_add(
    text:  str = typer.Argument(..., help="Memory content."),
    name:  str = typer.Option("",        "--name",  help="Memory name/key."),
    scope: str = typer.Option("project", "--scope", help="Scope: project | global | rule."),
    source: str = typer.Option("user", "--source", help="Recorded origin of the entry: user | import."),
) -> None:
    """Write a memory entry."""
    resolved = _resolve_scope(scope)
    if source not in (MemorySource.USER, MemorySource.IMPORT):
        raise typer.BadParameter(f"Unknown source '{source}'. Valid: user, import.")

    if not name:
        import time
        name = f"memory-{int(time.time())}"

    mgr    = MemoriesManager(_cwd())
    result = mgr.write(name, text, resolved, source=MemorySource(source))
    console.print(result)


@memory_app.command("remove")
def memory_remove(
    memory_id: str = typer.Argument(..., help="Memory name to remove."),
) -> None:
    """Delete a memory by name."""
    mgr = MemoriesManager(_cwd())
    try:
        result = mgr.delete(memory_id)
        console.print(result)
    except FileNotFoundError:
        console.print(f"[red]Memory '{memory_id}' not found.[/red]")
        raise typer.Exit(1) from None


@memory_app.command("purge")
def memory_purge(
    scope: str = typer.Option("project", "--scope", help="Scope to purge: project | global."),
) -> None:
    """Delete all memories in the given scope."""
    from nerdvana_cli.core import paths as core_paths

    resolved = _resolve_scope(scope)

    if resolved == MemoryScope.PROJECT_RULE:
        console.print("[yellow]Purge of PROJECT_RULE (NIRNA.md) is not supported via this command.[/yellow]")
        raise typer.Exit(1)

    if resolved == MemoryScope.PROJECT_KNOWLEDGE:
        target_dir = core_paths.project_memories_dir(_cwd())
    else:
        target_dir = core_paths.global_memories_dir()

    if not target_dir.exists() or not any(target_dir.rglob("*.md")):
        console.print(f"[dim]No memories to purge in scope '{scope}'.[/dim]")
        return

    count = sum(1 for _ in target_dir.rglob("*.md"))
    shutil.rmtree(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    console.print(f"Purged {count} memor{'y' if count == 1 else 'ies'} from scope '{scope}'.")


@memory_app.command("inbox")
def memory_inbox() -> None:
    """List the memories the agent proposed, each with a diff against the current entry."""
    _show(lambda: review.inbox_text(_cwd()))


@memory_app.command("approve")
def memory_approve(
    proposal_id: str | None = typer.Argument(None, help="Proposal id from 'memory inbox'."),
    everything:  bool       = typer.Option(False, "--all", help="Approve every pending proposal."),
) -> None:
    """Apply one proposal, or all of them with --all."""
    _show(lambda: review.approve_text(_cwd(), proposal_id, everything))


@memory_app.command("reject")
def memory_reject(
    proposal_id: str = typer.Argument(..., help="Proposal id from 'memory inbox'."),
) -> None:
    """Drop a proposal without applying it."""
    _show(lambda: review.reject_text(_cwd(), proposal_id))


@memory_app.command("forget")
def memory_forget(
    name: str  = typer.Argument(..., help="Memory name to delete."),
    yes:  bool = typer.Option(False, "--yes", "-y", help="Delete without asking."),
) -> None:
    """Delete a memory after confirmation and record it in the audit log."""
    entry = review.find_entry(_cwd(), name)
    if entry is None:
        console.print(f"[red]Memory '{escape(name)}' not found.[/red]")
        raise typer.Exit(1)
    if not yes:
        typer.confirm(f"Delete {review.describe_entry(entry)}?", abort=True)
    _show(lambda: review.forget_text(_cwd(), name))


@memory_app.command("stale")
def memory_stale(
    days:   int  = typer.Option(review.DEFAULT_STALE_DAYS, "--days", min=0, help="Not modified for this many days."),
    remove: bool = typer.Option(False, "--remove", help="Delete the listed memories after confirmation."),
    yes:    bool = typer.Option(False, "--yes", "-y", help="With --remove: delete without asking."),
) -> None:
    """List memories not modified for N days and never read; --remove deletes them after confirmation."""
    entries = review.stale_entries(_cwd(), days)
    console.print(review.stale_text(entries, days))
    if not remove or not entries:
        return
    if not yes:
        typer.confirm(f"Delete these {len(entries)} memories?", abort=True)
    _show(lambda: review.forget_stale_text(_cwd(), entries))
