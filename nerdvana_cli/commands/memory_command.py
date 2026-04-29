"""memory sub-app — project memory management.

Commands:
  memory list    — list memories by scope
  memory add     — write a new memory
  memory remove  — delete a memory by name
  memory purge   — delete all memories in a scope

Storage: core/memories.py MemoriesManager — same helper that /memories
slash command (memory_commands.py:handle_memories) uses.

Author: 최진호
Date:   2026-04-29
"""

from __future__ import annotations

import os
import shutil

import typer
from rich.console import Console

from nerdvana_cli.core.memories import MemoriesManager, MemoryScope

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
        dt = datetime.datetime.fromtimestamp(e.mtime).strftime("%Y-%m-%d")
        console.print(f"  [cyan]{e.name}[/cyan]  {e.size:>6}B  {dt}")


@memory_app.command("add")
def memory_add(
    text:  str = typer.Argument(..., help="Memory content."),
    name:  str = typer.Option("",        "--name",  help="Memory name/key."),
    scope: str = typer.Option("project", "--scope", help="Scope: project | global | rule."),
) -> None:
    """Write a memory entry."""
    resolved = _resolve_scope(scope)

    if not name:
        import time
        name = f"memory-{int(time.time())}"

    mgr    = MemoriesManager(_cwd())
    result = mgr.write(name, text, resolved)
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
