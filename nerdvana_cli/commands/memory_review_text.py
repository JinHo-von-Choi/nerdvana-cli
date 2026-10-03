"""Text of the memory review commands, shared by ``nerdvana memory`` and the ``/memory`` slash command.

Every function returns Rich markup with the dynamic parts escaped, so the same string can go
to a console or to the chat pane.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

import datetime
import shlex

from rich.markup import escape

from nerdvana_cli.core.context.memories import MemoriesManager, MemoryEntry
from nerdvana_cli.core.context.memory_review import (
    ChangeKind,
    MemoryInbox,
    ProposalNotFoundError,
    ProposalView,
    forget_memory,
)

USAGE = (
    "Usage: /memory <command>\n"
    "  inbox                      list the agent's proposals with a diff against the current entry\n"
    "  approve <id> | --all       apply one proposal, or every pending one\n"
    "  reject <id>                drop a proposal\n"
    "  forget <name> --yes        delete a memory\n"
    "  stale [--days N] [--remove --yes]   list memories not modified for N days (default 30) and never read"
)

DEFAULT_STALE_DAYS = 30


class ReviewError(ValueError):
    """A review command that cannot run; the message says why."""


def _day(timestamp: float) -> str:
    return datetime.datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d")


def _diff_lines(diff: str) -> list[str]:
    """Colour a unified diff: additions green, removals red, headers dim."""
    lines: list[str] = []
    for line in diff.splitlines():
        safe = escape(line)
        if line.startswith(("---", "+++", "@@")):
            lines.append(f"[dim]{safe}[/dim]")
        elif line.startswith("+"):
            lines.append(f"[green]{safe}[/green]")
        elif line.startswith("-"):
            lines.append(f"[red]{safe}[/red]")
        else:
            lines.append(safe)
    return lines


def _proposal_lines(view: ProposalView) -> list[str]:
    proposal = view.proposal
    head     = f"[bold]{proposal.id}[/bold]  {view.kind.value:<9}  {proposal.scope}  {escape(proposal.name)}"
    if view.kind == ChangeKind.UNCHANGED:
        return [head, "  [dim]same as the current entry[/dim]"]
    return [head, *_diff_lines(view.diff)]


def inbox_text(cwd: str) -> str:
    """Every pending proposal with its change kind and unified diff."""
    inbox     = MemoryInbox(cwd)
    proposals = inbox.pending()
    if not proposals:
        return "[dim]Memory inbox is empty.[/dim]"
    lines = [f"[bold]Memory inbox: {len(proposals)} pending[/bold]"]
    for proposal in proposals:
        lines.append("")
        lines.extend(_proposal_lines(inbox.view(proposal)))
    lines.append("")
    lines.append("Decide with: approve <id> | approve --all | reject <id>")
    return "\n".join(lines)


def approve_text(cwd: str, proposal_id: str | None, everything: bool) -> str:
    """Approve one proposal, or all of them. Raises ReviewError for a bad request or an unknown id."""
    if everything == (proposal_id is not None):
        raise ReviewError("Give either a proposal id or --all.")
    inbox = MemoryInbox(cwd)
    if proposal_id is not None:
        try:
            return f"Approved {escape(proposal_id)}: {escape(inbox.approve(proposal_id))}"
        except (ProposalNotFoundError, OSError, ValueError, NotImplementedError) as exc:
            raise ReviewError(str(exc)) from exc
    outcomes = inbox.approve_all()
    if not outcomes:
        return "[dim]Memory inbox is empty.[/dim]"
    failed = [o for o in outcomes if not o.ok]
    lines  = [
        f"Approved {o.proposal_id}: {escape(o.message)}" if o.ok
        else f"[red]Failed {o.proposal_id} ({escape(o.name)}): {escape(o.message)}[/red]"
        for o in outcomes
    ]
    lines.append(f"{len(outcomes) - len(failed)} approved, {len(failed)} failed.")
    return "\n".join(lines)


def reject_text(cwd: str, proposal_id: str) -> str:
    """Reject one proposal. Raises ReviewError for an unknown id."""
    try:
        return escape(MemoryInbox(cwd).reject(proposal_id))
    except (ProposalNotFoundError, OSError) as exc:
        raise ReviewError(str(exc)) from exc


def find_entry(cwd: str, name: str) -> MemoryEntry | None:
    """The live entry called *name* (the project one when both scopes have it), or None."""
    return next((e for e in MemoriesManager(cwd).list_memories() if e.name == name), None)


def describe_entry(entry: MemoryEntry) -> str:
    """One line naming an entry, its scope and its history."""
    return (
        f"{escape(entry.name)}  {entry.scope}  {entry.size}B  "
        f"created {_day(entry.created)}  modified {_day(entry.mtime)}  source {entry.source}"
    )


def forget_text(cwd: str, name: str) -> str:
    """Delete memory *name* and log it. Raises ReviewError when it does not exist."""
    entry = find_entry(cwd, name)
    if entry is None:
        raise ReviewError(f"Memory '{name}' not found.")
    try:
        return escape(forget_memory(cwd, name, entry.scope))
    except OSError as exc:
        raise ReviewError(str(exc)) from exc


def stale_entries(cwd: str, days: int) -> list[MemoryEntry]:
    """Memories not modified for *days* days that nothing has read."""
    return MemoriesManager(cwd).list_stale(days=days, never_loaded=True)


def stale_text(entries: list[MemoryEntry], days: int) -> str:
    """The stale report for *entries*."""
    header = f"[bold]Stale memories (not modified for {days}+ days, never read): {len(entries)}[/bold]"
    if not entries:
        return f"{header}\n  (none)"
    rows = [f"  {describe_entry(e)}" for e in entries]
    return "\n".join([header, *rows])


def forget_stale_text(cwd: str, entries: list[MemoryEntry]) -> str:
    """Forget every entry of a stale report and say how many went."""
    lines = [escape(forget_memory(cwd, entry.name, entry.scope)) for entry in entries]
    lines.append(f"Forgot {len(entries)} memor{'y' if len(entries) == 1 else 'ies'}.")
    return "\n".join(lines)


def _stale_days(tokens: list[str]) -> int:
    if "--days" not in tokens:
        return DEFAULT_STALE_DAYS
    at = tokens.index("--days") + 1
    if at >= len(tokens) or not tokens[at].isdigit():
        raise ReviewError("--days needs a whole number.")
    return int(tokens[at])


def _slash_stale(cwd: str, tokens: list[str]) -> str:
    days    = _stale_days(tokens)
    entries = stale_entries(cwd, days)
    report  = stale_text(entries, days)
    if "--remove" not in tokens or not entries:
        return report + (f"\nRemove them with: /memory stale --days {days} --remove --yes" if entries else "")
    if "--yes" not in tokens:
        return report + "\nNothing removed. Repeat with --yes to remove these memories."
    return report + "\n" + forget_stale_text(cwd, entries)


def _slash_forget(cwd: str, tokens: list[str]) -> str:
    names = [t for t in tokens if t != "--yes"]
    if len(names) != 1:
        raise ReviewError("Usage: /memory forget <name> --yes")
    entry = find_entry(cwd, names[0])
    if entry is None:
        raise ReviewError(f"Memory '{names[0]}' not found.")
    if "--yes" not in tokens:
        return f"Would delete: {describe_entry(entry)}\nRepeat with --yes to delete it."
    return forget_text(cwd, names[0])


def run_slash(cwd: str, args: str) -> str:
    """Run the arguments of a ``/memory`` command and return the chat text. Raises ReviewError."""
    try:
        tokens = shlex.split(args)
    except ValueError as exc:
        raise ReviewError(f"Cannot read the arguments: {exc}") from exc
    if not tokens or tokens[0] == "help":
        return escape(USAGE)
    command, rest = tokens[0], tokens[1:]
    if command == "inbox":
        return inbox_text(cwd)
    if command == "approve":
        ids        = [t for t in rest if t != "--all"]
        everything = "--all" in rest
        if len(ids) > 1 or (everything and ids):
            raise ReviewError("Usage: /memory approve <id> | /memory approve --all")
        return approve_text(cwd, ids[0] if ids else None, everything)
    if command == "reject":
        if len(rest) != 1:
            raise ReviewError("Usage: /memory reject <id>")
        return reject_text(cwd, rest[0])
    if command == "forget":
        return _slash_forget(cwd, rest)
    if command == "stale":
        return _slash_stale(cwd, rest)
    raise ReviewError(f"Unknown subcommand '{command}'.\n{USAGE}")
