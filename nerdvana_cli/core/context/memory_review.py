"""Reviewable memory: an inbox of agent proposals, forgetting and the audit log.

With ``memory.review`` on, a memory the agent writes, edits or deletes is not applied.
It becomes a proposal file in ``<cwd>/.nerdvana/memory-inbox/`` that no prompt hint and no
memory tool reads. The user lists the proposals with a unified diff against the live
entry and approves or rejects each one. Approval, rejection and forgetting are appended
to ``~/.nerdvana/logs/memory-audit.jsonl``.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

import difflib
import fcntl
import json
import logging
import os
import re
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from nerdvana_cli.core.config import paths as core_paths
from nerdvana_cli.core.context.memories import MemoriesManager, MemoryScope
from nerdvana_cli.core.context.memory_index import MemorySource

logger = logging.getLogger(__name__)

# Most proposals that may wait in one project's inbox.
MAX_PENDING = 200

_PROPOSAL_ID = re.compile(r"^[0-9a-f]{8}$")


class ProposalAction(StrEnum):
    """What an approved proposal does."""

    WRITE  = "write"
    DELETE = "delete"


class ChangeKind(StrEnum):
    """How a proposal differs from the live entry."""

    NEW       = "new"
    CHANGED   = "changed"
    REMOVED   = "removed"
    UNCHANGED = "unchanged"


class ProposalNotFoundError(LookupError):
    """No pending proposal has the given id."""


@dataclass(frozen=True)
class Proposal:
    """One memory change the agent proposed and the user has not decided yet."""

    id:          str
    name:        str
    scope:       MemoryScope
    action:      ProposalAction
    content:     str
    proposed_at: float

    def queued_message(self) -> str:
        """What the agent is told when the proposal is stored."""
        verb = "deletion of" if self.action == ProposalAction.DELETE else "change to"
        return (
            f"Queued proposal {self.id}: {verb} memory '{self.name}' [{self.scope}]. "
            "It awaits the user's review and takes no effect until approved "
            "(memory.review is on); ReadMemory and ListMemories do not show it yet."
        )


@dataclass(frozen=True)
class ProposalView:
    """A proposal with its classification and its unified diff against the live entry."""

    proposal: Proposal
    kind:     ChangeKind
    diff:     str


@dataclass(frozen=True)
class Outcome:
    """Result of approving one proposal in a batch."""

    proposal_id: str
    name:        str
    ok:          bool
    message:     str


def audit(action: str, **fields: Any) -> None:
    """Append one record to the memory audit log."""
    path = core_paths.memory_audit_log()
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {"ts": datetime.now(UTC).isoformat(timespec="seconds"), "action": action, **fields}
    with open(path, "a", encoding="utf-8") as fp:
        fcntl.flock(fp, fcntl.LOCK_EX)
        try:
            fp.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
            fp.flush()
        finally:
            fcntl.flock(fp, fcntl.LOCK_UN)


def forget_memory(cwd: str, name: str, scope: MemoryScope | None = None) -> str:
    """Delete memory *name* (from *scope*, or the first scope holding it) and log the forget.

    Raises FileNotFoundError when no such memory exists.
    """
    mgr   = MemoriesManager(cwd)
    found = scope if scope is not None else mgr.scope_of(name)
    if found is None:
        raise FileNotFoundError(f"Memory '{name}' not found.")
    message = mgr.delete(name, found)
    audit("forget", name=name, scope=found.value, project=cwd)
    return message


class MemoryInbox:
    """The proposals of one project awaiting review."""

    def __init__(self, cwd: str) -> None:
        self._cwd = cwd
        self._mgr = MemoriesManager(cwd)
        self._dir = core_paths.project_memory_inbox_dir(cwd)

    # ------------------------------------------------------------------
    # Proposing (the agent side)
    # ------------------------------------------------------------------

    def propose_write(self, name: str, content: str, scope: MemoryScope) -> Proposal:
        """Queue a write of *content* as *name*.

        Raises the errors of ``MemoriesManager.write`` for an unusable name, scope or size,
        and ValueError when the inbox is full.
        """
        self._mgr.validate_write(name, content, scope)
        return self._store(name, scope, ProposalAction.WRITE, content)

    def propose_delete(self, name: str) -> Proposal:
        """Queue the deletion of memory *name*. Raises FileNotFoundError when it does not exist."""
        scope = self._mgr.scope_of(name)
        if scope is None:
            raise FileNotFoundError(f"Memory '{name}' not found.")
        return self._store(name, scope, ProposalAction.DELETE, "")

    def propose_edit(self, name: str, needle: str, repl: str, mode: str = "literal") -> Proposal:
        """Queue the search-and-replace of ``MemoriesManager.edit`` as a write of its result.

        Raises FileNotFoundError when *name* does not exist and ValueError for a bad *mode*.
        """
        scope, updated, _ = self._mgr.preview_edit(name, needle, repl, mode)
        return self.propose_write(name, updated, scope)

    def propose_rename(self, old_name: str, new_name: str, new_scope: MemoryScope | None = None) -> list[Proposal]:
        """Queue a rename as a write of the new name followed by a deletion of the old one.

        Raises FileNotFoundError when *old_name* does not exist and ValueError when the
        rename would not move the entry.
        """
        scope = self._mgr.scope_of(old_name)
        if scope is None:
            raise FileNotFoundError(f"Memory '{old_name}' not found.")
        target = new_scope if new_scope is not None else scope
        if (new_name, target) == (old_name, scope):
            raise ValueError("The new name and scope are the same as the current ones.")
        content = self._mgr.peek(old_name, scope)
        assert content is not None
        return [self.propose_write(new_name, content, target), self.propose_delete(old_name)]

    def _store(self, name: str, scope: MemoryScope, action: ProposalAction, content: str) -> Proposal:
        """Write a new proposal file and return the proposal."""
        self._dir.mkdir(parents=True, exist_ok=True)
        if len(list(self._dir.glob("*.json"))) >= MAX_PENDING:
            raise ValueError(
                f"The memory inbox holds {MAX_PENDING} proposals already; "
                "the user must review them before more can be queued."
            )
        proposal_id = uuid.uuid4().hex[:8]
        while (self._dir / f"{proposal_id}.json").exists():
            proposal_id = uuid.uuid4().hex[:8]
        proposal = Proposal(proposal_id, name, scope, action, content, time.time())
        tmp = self._dir / f"{proposal_id}.json.tmp"
        tmp.write_text(json.dumps(asdict(proposal), ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, self._dir / f"{proposal_id}.json")
        return proposal

    # ------------------------------------------------------------------
    # Reviewing (the user side)
    # ------------------------------------------------------------------

    def pending(self) -> list[Proposal]:
        """Every readable proposal, oldest first. An unreadable file is logged and skipped."""
        found: list[Proposal] = []
        for path in self._dir.glob("*.json"):
            try:
                found.append(_load(path))
            except (OSError, ValueError, KeyError, TypeError) as exc:
                logger.warning("Ignoring unreadable memory proposal %s: %s", path, exc)
        return sorted(found, key=lambda p: (p.proposed_at, p.id))

    def get(self, proposal_id: str) -> Proposal:
        """The proposal with *proposal_id*. Raises ProposalNotFoundError when there is none."""
        path = self._path(proposal_id)
        try:
            return _load(path)
        except FileNotFoundError:
            raise ProposalNotFoundError(f"No pending memory proposal '{proposal_id}'.") from None

    def view(self, proposal: Proposal) -> ProposalView:
        """Classify *proposal* and diff it against the live entry."""
        current = self._current(proposal)
        if proposal.action == ProposalAction.DELETE:
            kind, proposed = ChangeKind.REMOVED, ""
        elif current is None:
            kind, proposed = ChangeKind.NEW, proposal.content
        else:
            kind     = ChangeKind.UNCHANGED if current == proposal.content else ChangeKind.CHANGED
            proposed = proposal.content
        label = f"{proposal.scope}/{proposal.name}"
        diff  = difflib.unified_diff(
            (current or "").splitlines(),
            proposed.splitlines(),
            fromfile = f"current/{label}" if current is not None else "(none)",
            tofile   = f"proposed/{label}" if kind != ChangeKind.REMOVED else "(removed)",
            lineterm = "",
        )
        return ProposalView(proposal, kind, "\n".join(diff))

    def approve(self, proposal_id: str) -> str:
        """Apply the proposal, log the approval and drop the proposal file.

        Raises ProposalNotFoundError for an unknown id, and whatever the write or delete
        raises (the proposal then stays pending).
        """
        proposal = self.get(proposal_id)
        kind     = self.view(proposal).kind
        if proposal.action == ProposalAction.DELETE:
            message = self._mgr.delete(proposal.name, proposal.scope)
        else:
            message = self._mgr.write(proposal.name, proposal.content, proposal.scope, source=MemorySource.AGENT)
        self._decided("approve", proposal, kind)
        return message

    def reject(self, proposal_id: str) -> str:
        """Drop the proposal without applying it and log the rejection."""
        proposal = self.get(proposal_id)
        self._decided("reject", proposal, self.view(proposal).kind)
        return f"Rejected proposal {proposal.id} for memory '{proposal.name}'."

    def approve_all(self) -> list[Outcome]:
        """Approve every pending proposal, oldest first; a failing one stays pending."""
        outcomes: list[Outcome] = []
        for proposal in self.pending():
            try:
                outcomes.append(Outcome(proposal.id, proposal.name, True, self.approve(proposal.id)))
            except (OSError, ValueError, LookupError, NotImplementedError) as exc:
                outcomes.append(Outcome(proposal.id, proposal.name, False, str(exc)))
        return outcomes

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _path(self, proposal_id: str) -> Path:
        """File of *proposal_id*; an id that is not eight lowercase hex digits names no file."""
        if not _PROPOSAL_ID.match(proposal_id):
            raise ProposalNotFoundError(f"No pending memory proposal '{proposal_id}'.")
        return self._dir / f"{proposal_id}.json"

    def _current(self, proposal: Proposal) -> str | None:
        """Live content the proposal would replace; None for a new entry or an appended rule."""
        if proposal.scope == MemoryScope.PROJECT_RULE:
            return None
        return self._mgr.peek(proposal.name, proposal.scope)

    def _decided(self, action: str, proposal: Proposal, kind: ChangeKind) -> None:
        """Log the decision, then remove the proposal file."""
        audit(
            action,
            name     = proposal.name,
            scope    = proposal.scope.value,
            proposal = proposal.id,
            change   = kind.value,
            project  = self._cwd,
        )
        self._path(proposal.id).unlink(missing_ok=True)


def _load(path: Path) -> Proposal:
    """Parse one proposal file. Raises ValueError, KeyError or TypeError when it is malformed."""
    if not _PROPOSAL_ID.match(path.stem):
        raise ValueError(f"'{path.name}' is not a proposal file name")
    raw = json.loads(path.read_text(encoding="utf-8"))
    return Proposal(
        id          = path.stem,
        name        = str(raw["name"]),
        scope       = MemoryScope(raw["scope"]),
        action      = ProposalAction(raw["action"]),
        content     = str(raw["content"]),
        proposed_at = float(raw["proposed_at"]),
    )
