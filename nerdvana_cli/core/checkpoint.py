"""Git Checkpoint system — Phase E.

Automatically saves a git stash before every file-editing tool call.
Provides /undo, /redo, /checkpoints REPL commands.

Design
------
- Uses ``git stash push --keep-index --include-untracked`` so the working tree
  is preserved after the stash is created.
- Each stash message follows: ``nerdvana:<session_id>:<edit_id>``
- Session-scoped LRU: when per_session_max is reached, the oldest
  session-owned stash is dropped.
- Non-git directories are silently skipped (no errors raised).

Author: 최진호
Date:   2026-04-18
"""

from __future__ import annotations

import logging
import re
import subprocess
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# Stash message prefix — also used as filter pattern
_STASH_PREFIX = "nerdvana:"
_STASH_MSG_RE = re.compile(r"^nerdvana:(?P<session>[^:]+):(?P<edit_id>\d+)$")


# ---------------------------------------------------------------------------
# Data class
# ---------------------------------------------------------------------------

@dataclass
class StashEntry:
    """Parsed representation of a nerdvana stash entry."""

    stash_ref: str   # e.g. "stash@{0}"
    session:   str   # session_id
    edit_id:   int   # monotonic edit counter within the session
    message:   str   # full stash message


# ---------------------------------------------------------------------------
# Low-level git helpers
# ---------------------------------------------------------------------------

def _is_git_repo(cwd: str) -> bool:
    """Return True if *cwd* is inside a git repository."""
    result = subprocess.run(
        ["git", "rev-parse", "--git-dir"],
        cwd=cwd,
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def _run_git(args: list[str], cwd: str) -> tuple[int, str, str]:
    """Run a git command and return (returncode, stdout, stderr)."""
    result = subprocess.run(
        ["git"] + args,
        cwd=cwd,
        capture_output=True,
        text=True,
    )
    return result.returncode, result.stdout.strip(), result.stderr.strip()


def _list_all_stashes(cwd: str) -> list[StashEntry]:
    """Return all git stashes, including non-nerdvana ones, as StashEntry list.

    Non-nerdvana stashes have session="<external>" and edit_id=-1.
    """
    code, out, _ = _run_git(
        ["stash", "list", "--format=%gd|%gs"],
        cwd=cwd,
    )
    if code != 0 or not out:
        return []

    entries: list[StashEntry] = []
    for line in out.splitlines():
        if "|" not in line:
            continue
        ref, msg = line.split("|", 1)
        # "stash@{0}: On branch: nerdvana:..." — isolate our part
        # git stash list shows "stash@{N}: On <branch>: <message>"
        # or "stash@{N}: WIP on <branch>: <hash> <message>"
        # We use the custom message from --format=%gs which is just the subject
        m = _STASH_MSG_RE.search(msg)
        if m:
            entries.append(StashEntry(
                stash_ref = ref.strip(),
                session   = m.group("session"),
                edit_id   = int(m.group("edit_id")),
                message   = msg.strip(),
            ))
    return entries


def _list_session_stashes(cwd: str, session_id: str) -> list[StashEntry]:
    """Return stashes belonging to *session_id*, sorted ascending by edit_id."""
    all_stashes = _list_all_stashes(cwd)
    owned = [s for s in all_stashes if s.session == session_id]
    return sorted(owned, key=lambda s: s.edit_id)


# ---------------------------------------------------------------------------
# CheckpointManager
# ---------------------------------------------------------------------------

class CheckpointManager:
    """Manages git stash checkpoints for a session.

    Args:
        cwd:             Working directory (project root).
        session_id:      Unique session identifier.
        per_session_max: Maximum stashes to keep per session (LRU eviction).
        enabled:         If False, all operations are no-ops.
    """

    def __init__(
        self,
        cwd:             str,
        session_id:      str,
        per_session_max: int  = 50,
        enabled:         bool = True,
    ) -> None:
        self._cwd             = cwd
        self._session_id      = session_id
        self._per_session_max = per_session_max
        self._enabled         = enabled
        self._edit_counter    = 0

        # Redo stack: list of stash refs that were undone in this session
        self._redo_stack: list[str] = []

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def before_edit(self, tool_name: str) -> str | None:
        """Create a stash checkpoint before an edit tool runs.

        Returns the stash ref created, or None if skipped (non-git / disabled).
        """
        if not self._enabled:
            return None
        if not _is_git_repo(self._cwd):
            return None

        self._edit_counter += 1
        edit_id = self._edit_counter
        msg     = f"{_STASH_PREFIX}{self._session_id}:{edit_id}"

        code, out, err = _run_git(
            [
                "stash", "push",
                "--keep-index",
                "--include-untracked",
                "--message", msg,
            ],
            cwd=self._cwd,
        )
        if code != 0:
            logger.debug("checkpoint: stash push failed for %s: %s", tool_name, err)
            return None

        if "No local changes to save" in out:
            logger.debug("checkpoint: nothing to stash before %s", tool_name)
            return None

        stash_ref = self._find_stash_by_message(msg)
        if not stash_ref:
            # Message not found — stash push silently did nothing
            return None
        logger.debug("checkpoint: created %s before %s", stash_ref, tool_name)
        self._evict_old_stashes()
        # New edit clears redo stack
        self._redo_stack.clear()
        return stash_ref

    def undo(self) -> str:
        """Pop the most recent session stash and apply it (hard reset + apply).

        Returns a human-readable status message.
        Raises RuntimeError if there is nothing to undo.
        """
        if not _is_git_repo(self._cwd):
            return "Not a git repository — undo unavailable."

        stashes = _list_session_stashes(self._cwd, self._session_id)
        if not stashes:
            return "Nothing to undo."

        latest = stashes[-1]
        # Apply: pop the stash (restores working tree, removes stash entry)
        code, _out, err = _run_git(
            ["stash", "pop", "--index", latest.stash_ref],
            cwd=self._cwd,
        )
        if code != 0:
            return f"Undo failed: {err}"

        self._redo_stack.append(latest.stash_ref)
        return f"Undone checkpoint {latest.stash_ref} (edit #{latest.edit_id})."

    def redo(self) -> str:
        """Re-apply the last undone checkpoint.

        Note: after a stash pop, the entry no longer exists in git stash list.
        Redo in this implementation re-creates a stash from the working tree
        state.  This is a best-effort operation.

        Returns a human-readable status message.
        """
        if not _is_git_repo(self._cwd):
            return "Not a git repository — redo unavailable."

        # Simple redo: stash current working tree changes again
        if not self._redo_stack:
            return "Nothing to redo."

        self._redo_stack.pop()
        self._edit_counter += 1
        edit_id = self._edit_counter
        msg     = f"{_STASH_PREFIX}{self._session_id}:{edit_id}"
        code, out, err = _run_git(
            ["stash", "push", "--keep-index", "--include-untracked", "--message", msg],
            cwd=self._cwd,
        )
        if code != 0:
            return f"Redo failed: {err}"
        if "No local changes to save" in out:
            return "No changes to redo."
        return f"Redo: saved new checkpoint {msg}."

    def list_checkpoints(self) -> list[StashEntry]:
        """Return session-owned stash entries sorted by edit_id."""
        if not _is_git_repo(self._cwd):
            return []
        return _list_session_stashes(self._cwd, self._session_id)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _find_stash_by_message(self, msg: str) -> str:
        """Return the stash ref whose message matches *msg*, or empty string."""
        all_stashes = _list_all_stashes(self._cwd)
        for entry in all_stashes:
            if entry.message.endswith(msg):
                return entry.stash_ref
        return ""

    def _evict_old_stashes(self) -> None:
        """Drop oldest session stashes when count exceeds per_session_max."""
        stashes = _list_session_stashes(self._cwd, self._session_id)
        excess  = len(stashes) - self._per_session_max
        if excess <= 0:
            return
        for entry in stashes[:excess]:
            code, _, err = _run_git(["stash", "drop", entry.stash_ref], cwd=self._cwd)
            if code != 0:
                logger.debug("checkpoint: failed to drop %s: %s", entry.stash_ref, err)
            else:
                logger.debug("checkpoint: evicted %s (LRU)", entry.stash_ref)
