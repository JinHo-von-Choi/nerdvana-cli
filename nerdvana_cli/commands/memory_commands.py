"""Memory and checkpoint slash command handlers — Phase E.

Commands:
  /undo           — Restore the previous pre-edit checkpoint
  /redo           — Re-apply last undone checkpoint
  /checkpoints    — List session checkpoints
  /memories       — List project memories (with optional --stale flag)
  /memory         : Review the agent's memory proposals, forget memories, report stale ones
  /route-knowledge — Classify content and suggest WriteMemory scope

Author: 최진호
Date:   2026-04-18
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from rich.markup import escape

if TYPE_CHECKING:
    from nerdvana_cli.ui.app import NerdvanaApp

# ---------------------------------------------------------------------------
# /undo
# ---------------------------------------------------------------------------

async def handle_undo(app: NerdvanaApp, args: str) -> None:
    """Handle /undo — restore the previous pre-edit checkpoint."""
    cp = getattr(app, "_checkpoint_manager", None)
    if cp is None:
        app._add_chat_message("[yellow]Checkpoint manager not available.[/yellow]")
        return
    msg = cp.undo()
    app._add_chat_message(f"[green]{msg}[/green]" if "Undone" in msg else f"[yellow]{msg}[/yellow]")


# ---------------------------------------------------------------------------
# /redo
# ---------------------------------------------------------------------------

async def handle_redo(app: NerdvanaApp, args: str) -> None:
    """Handle /redo — re-apply the last undone checkpoint."""
    cp = getattr(app, "_checkpoint_manager", None)
    if cp is None:
        app._add_chat_message("[yellow]Checkpoint manager not available.[/yellow]")
        return
    msg = cp.redo()
    app._add_chat_message(f"[green]{msg}[/green]" if "checkpoint" in msg else f"[yellow]{msg}[/yellow]")


# ---------------------------------------------------------------------------
# /checkpoints
# ---------------------------------------------------------------------------

# CheckpointEntry.kind for a git stash an earlier build left behind. Such a row
# is informational: undo never consumes it, so the listing has to say so.
_LEGACY_STASH_KIND = "legacy-stash"


async def handle_checkpoints(app: NerdvanaApp, args: str) -> None:
    """Handle /checkpoints — list this session's restorable checkpoints."""
    cp = getattr(app, "_checkpoint_manager", None)
    if cp is None:
        app._add_chat_message("[yellow]Checkpoint manager not available.[/yellow]")
        return
    entries = cp.list_checkpoints()
    if not entries:
        app._add_chat_message("[dim]No checkpoints in this session.[/dim]")
        return

    lines = [f"[bold]Session checkpoints ({len(entries)})[/bold]"]
    for e in reversed(entries):  # newest first
        row = f"  {e.checkpoint_id:<14}  edit #{e.edit_id:<4}"
        if e.kind == _LEGACY_STASH_KIND:
            row += "  [yellow]git stash from an earlier build; /undo skips it[/yellow]"
        lines.append(row)
    app._add_chat_message("\n".join(lines))


# ---------------------------------------------------------------------------
# /memories
# ---------------------------------------------------------------------------

async def handle_memories(app: NerdvanaApp, args: str) -> None:
    """Handle /memories [--stale [--days N]] — list project memories."""
    import datetime

    from nerdvana_cli.core.context.memories import MemoriesManager

    stale   = "--stale" in args
    days    = 30
    days_m  = re.search(r"--days\s+(\d+)", args)
    if days_m:
        days = int(days_m.group(1))

    mgr = MemoriesManager(app.settings.cwd)

    if stale:
        entries = mgr.list_stale(days=days)
        header  = f"[bold]Stale memories (>= {days} days old) — {len(entries)} found[/bold]"
    else:
        entries = mgr.list_memories()
        header  = f"[bold]Project memories — {len(entries)} found[/bold]"

    if not entries:
        app._add_chat_message(f"{header}\n  (none)")
        return

    lines = [header]
    for e in entries:
        dt  = datetime.datetime.fromtimestamp(e.mtime).strftime("%Y-%m-%d")
        lines.append(f"  {e.name:<40}  {e.scope:<20}  {e.size:>6}B  {dt}  {e.source}")
    app._add_chat_message("\n".join(lines))


# ---------------------------------------------------------------------------
# /memory
# ---------------------------------------------------------------------------

async def handle_memory(app: NerdvanaApp, args: str) -> None:
    """Handle /memory inbox | approve | reject | forget | stale, see memory_review_text.USAGE."""
    from nerdvana_cli.commands.memory_review_text import ReviewError, run_slash

    try:
        app._add_chat_message(run_slash(app.settings.cwd, args))
    except ReviewError as exc:
        app._add_chat_message(f"[red]{escape(str(exc))}[/red]")


# ---------------------------------------------------------------------------
# /route-knowledge
# ---------------------------------------------------------------------------

_RULE_PATTERNS = re.compile(
    r"\b(must|shall|forbidden|禁止|금지|반드시|절대|always|never|required)\b",
    re.IGNORECASE,
)
_KNOWLEDGE_PATTERNS = re.compile(
    r"\b(build|test|compile|import|module|package|directory|structure|architecture|config)\b",
    re.IGNORECASE,
)
_PREFERENCE_PATTERNS = re.compile(
    r"\b(prefer|선호|style|스타일|like|want|instead of|rather)\b",
    re.IGNORECASE,
)
_EXPERIENCE_PATTERNS = re.compile(
    r"\b(error|에러|오류|exception|traceback|fail|해결|fix|resolved|bug)\b",
    re.IGNORECASE,
)


# MemoryScope.AGENT_EXPERIENCE. WriteMemory rejects it: those memories live in
# AnchorMind, so the suggestion has to point there instead of at a call that
# always fails.
_AGENT_EXPERIENCE = "agent_experience"


def _score_scopes(content: str) -> dict[str, int]:
    """Return the per-scope pattern-hit counts for *content*."""
    return {
        "project_rule":      len(_RULE_PATTERNS.findall(content)),
        "project_knowledge": len(_KNOWLEDGE_PATTERNS.findall(content)),
        "user_global":       len(_PREFERENCE_PATTERNS.findall(content)),
        _AGENT_EXPERIENCE:   len(_EXPERIENCE_PATTERNS.findall(content)),
    }


def _classify_scope(content: str) -> str:
    """Return the best-guess MemoryScope value for *content*."""
    scores = _score_scopes(content)
    best   = max(scores, key=lambda k: scores[k])
    if scores[best] == 0:
        return "project_knowledge"  # safe default
    return best


async def handle_route_knowledge(app: NerdvanaApp, args: str) -> None:
    """Handle /route-knowledge <content> — classify and suggest scope.

    Usage: /route-knowledge <text to classify>
    The agent should then call WriteMemory with the suggested scope.
    """
    content = args.strip()
    if not content:
        app._add_chat_message(
            "[bold]/route-knowledge[/bold]\n"
            "Usage: /route-knowledge <content to classify>\n\n"
            "Analyzes content and suggests the appropriate MemoryScope.\n"
            "The agent then calls WriteMemory with the suggested scope.\n\n"
            "Scopes:\n"
            "  project_rule      — rules for the codebase (must/shall/forbidden)\n"
            "  project_knowledge — build/structure/architecture facts\n"
            "  user_global       — personal preferences and style\n"
            "  agent_experience  — errors and solutions, recorded through\n"
            "                      AnchorMind rather than WriteMemory"
        )
        return

    suggested = _classify_scope(content)

    # Show scores for transparency
    scores = _score_scopes(content)
    score_lines = "\n".join(
        f"  {'>' if k == suggested else ' '} {k:<22}  score={v}"
        for k, v in scores.items()
    )

    if suggested == _AGENT_EXPERIENCE:
        action = (
            "This content belongs to AnchorMind, which owns experience memories.\n"
            "WriteMemory does not store this scope. Record it with:\n"
            "  mcp__anchormind__remember(type='error', content='...')"
        )
    else:
        action = (
            "To store, call:\n"
            f"  WriteMemory(name='<name>', content='...', scope='{suggested}')"
        )

    msg = (
        f"[bold]Suggested scope:[/bold] [green]{suggested}[/green]\n\n"
        f"Scores:\n{score_lines}\n\n"
        f"{action}"
    )
    app._add_chat_message(msg)


# ---------------------------------------------------------------------------
# /rewind
# ---------------------------------------------------------------------------

async def handle_rewind(app: NerdvanaApp, args: str) -> None:
    """Handle /rewind [N] — go back before the last N prompts, undoing their file edits."""
    loop = getattr(app, "_agent_loop", None)
    if loop is None or getattr(app, "_is_generating", False):
        app._add_chat_message("[dim]/rewind works while the agent is idle.[/dim]")
        return
    text = args.strip()
    if text and not (text.isdigit() and int(text) >= 1):
        app._add_chat_message("[red]Usage: /rewind [N][/red]")
        return
    app._add_chat_message(f"[dim]{loop.rewind(int(text) if text else 1)}[/dim]")
