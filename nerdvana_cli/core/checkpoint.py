"""Edit checkpoints backed by per-file copies.

Before an editing tool runs, the manager copies the files that tool is about to
touch into a session-private snapshot directory. The working tree itself is
never moved, reverted or emptied, so uncommitted user work stays exactly where
the user left it.

Design
------
- Snapshots live under ``user_cache_dir()/checkpoints/<session>/<edit_id>``:
  one manifest plus one blob per captured file.
- A path that does not exist yet is recorded as absent, so undo deletes the
  file the edit created instead of restoring stale content.
- ``undo`` captures the current contents of the same paths before restoring,
  and ``redo`` replays that capture. Both touch only the captured paths.
- Session-scoped LRU: the oldest snapshot is dropped once ``per_session_max``
  is exceeded, and session directories left untouched for a week are pruned.
- Large files and oversized edit batches are skipped rather than copied, so the
  store cannot grow without bound.
- Non-git directories are skipped.
- Stashes written by an earlier checkpoint build may still hold work that was
  taken out of a working tree. They are listed and logged so the user can
  recover them, and never dropped: only the user knows what is in them.

Author:  최진호
Date:    2026-04-18
Updated: 2026-09-10
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from nerdvana_cli.core.paths import user_cache_dir

logger = logging.getLogger(__name__)

# Message format written by the earlier stash-based build, still recognised so
# leftover stashes can be surfaced to the user. git prefixes the subject with
# "On <branch>: ", so the marker is matched anywhere in the line.
_STASH_MSG_RE = re.compile(r"nerdvana:(?P<session>[^:\s]+):(?P<edit_id>\d+)\s*$")

_SNAPSHOT_DIRNAME   = "checkpoints"
_REDO_DIRNAME       = "redo"
_BLOB_DIRNAME       = "files"
_MANIFEST_NAME      = "manifest.json"

# Guards against an unbounded store: one oversized file or a huge edit batch is
# recorded as skipped instead of copied.
_MAX_FILE_BYTES     = 5 * 1024 * 1024
_MAX_FILES_PER_EDIT = 64

# Session directories older than this are removed on the next capture. Dropping
# one costs undo depth only: every captured file is still in the working tree.
_SESSION_TTL_SECS   = 7 * 24 * 60 * 60

_UNSAFE_NAME_RE     = re.compile(r"[^A-Za-z0-9._-]")

# CheckpointEntry.kind values.
_KIND_SNAPSHOT      = "snapshot"
_KIND_LEGACY_STASH  = "legacy-stash"


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class StashEntry:
    """Parsed representation of a git stash written by an earlier build."""

    stash_ref: str   # e.g. "stash@{0}"
    session:   str   # session_id
    edit_id:   int   # monotonic edit counter within the session
    message:   str   # full stash message


@dataclass
class CheckpointEntry:
    """One restorable checkpoint.

    ``kind`` is ``snapshot`` for a file copy this build owns, and
    ``legacy-stash`` for a git stash an earlier build left behind. A legacy row
    is informational: it carries no captured paths, undo never consumes it, and
    only the user can decide what to do with its contents.
    """

    checkpoint_id: str
    session:       str
    edit_id:       int
    paths:         tuple[str, ...]
    kind:          str = _KIND_SNAPSHOT

    @property
    def stash_ref(self) -> str:
        """Label rendered by the /checkpoints listing."""
        return self.checkpoint_id


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
    """Return every stash whose message follows the nerdvana format."""
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
# Snapshot helpers
# ---------------------------------------------------------------------------

def _sanitize(name: str) -> str:
    """Reduce *name* to a safe single path component."""
    cleaned = _UNSAFE_NAME_RE.sub("_", name).strip("._")
    return cleaned[:64] or "session"


def _session_root(session_id: str) -> Path:
    """Directory holding every snapshot of one session."""
    return user_cache_dir() / _SNAPSHOT_DIRNAME / _sanitize(session_id)


def _remove_tree(directory: Path) -> None:
    """Delete *directory* and everything under it, ignoring absence."""
    shutil.rmtree(directory, ignore_errors=True)


def _read_manifest(directory: Path) -> dict[str, Any] | None:
    """Return the manifest stored in *directory*, or None when unreadable."""
    try:
        raw = (directory / _MANIFEST_NAME).read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _write_manifest(
    directory: Path,
    session:   str,
    edit_id:   int,
    records:   list[dict[str, Any]],
) -> None:
    """Persist the description of a capture next to its blobs."""
    payload: dict[str, Any] = {
        "session": session,
        "edit_id": edit_id,
        "created": time.time(),
        "entries": records,
    }
    directory.mkdir(parents=True, exist_ok=True)
    (directory / _MANIFEST_NAME).write_text(
        json.dumps(payload, ensure_ascii=False),
        encoding="utf-8",
    )


def _capture_paths(paths: Sequence[str], directory: Path) -> list[dict[str, Any]]:
    """Copy every path in *paths* into *directory* and describe the result.

    A path that is missing is recorded as absent so it can be deleted again on
    restore. Anything that is not a regular file, and any file above the size
    cap, is recorded as skipped and left alone by restore.
    """
    blob_dir = directory / _BLOB_DIRNAME
    blob_dir.mkdir(parents=True, exist_ok=True)

    records: list[dict[str, Any]] = []
    for index, raw_path in enumerate(paths):
        target = Path(raw_path)
        record: dict[str, Any] = {
            "path":    str(target),
            "existed": False,
            "blob":    None,
            "skipped": False,
        }
        if target.is_symlink() or (target.exists() and not target.is_file()):
            record["skipped"] = True
        elif target.is_file():
            if target.stat().st_size > _MAX_FILE_BYTES:
                record["skipped"] = True
                logger.debug("checkpoint: %s exceeds the size cap, not captured", target)
            else:
                blob_name = f"{index:03d}"
                shutil.copy2(target, blob_dir / blob_name)
                record["existed"] = True
                record["blob"]    = blob_name
        records.append(record)
    return records


def _restore_records(records: list[dict[str, Any]], directory: Path) -> tuple[int, int]:
    """Put every captured path back. Returns (restored, skipped) counts."""
    blob_dir = directory / _BLOB_DIRNAME
    restored = 0
    skipped  = 0

    for record in records:
        raw_path = str(record.get("path") or "")
        if not raw_path or record.get("skipped"):
            skipped += 1
            continue

        target    = Path(raw_path)
        blob_name = record.get("blob")

        if record.get("existed") and blob_name:
            source = blob_dir / str(blob_name)
            if not source.is_file():
                skipped += 1
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        elif target.is_symlink() or target.is_file():
            target.unlink()
        restored += 1

    return restored, skipped


# ---------------------------------------------------------------------------
# CheckpointManager
# ---------------------------------------------------------------------------

class CheckpointManager:
    """Manages per-file edit checkpoints for a session.

    Args:
        cwd:             Working directory (project root).
        session_id:      Unique session identifier.
        per_session_max: Maximum snapshots to keep per session (LRU eviction).
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
        self._per_session_max = max(1, per_session_max)
        self._enabled         = enabled
        self._root            = _session_root(session_id)

        # Highest edit_id handed out so far; resolved from disk on first use.
        self._edit_counter: int | None = None

        # Snapshots of undone edits, newest last.
        self._redo_stack: list[Path] = []

        self._legacy_checked = False
        self._pruned         = False
        self._inert_reported = False

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def before_edit(self, tool_name: str, paths: Sequence[str] | None = None) -> str | None:
        """Copy the files *tool_name* is about to change into a snapshot.

        The caller must pass the paths the tool is about to write. This is what
        turns the feature on: with no paths there is nothing to copy, no
        checkpoint is taken, and undo and redo have nothing to restore. A call
        that omits them is reported once per session at warning level, because
        an undo facility that looks armed and captures nothing is the same
        data-loss risk it exists to remove.

        The working tree is left untouched, so unstaged edits and untracked
        files survive the checkpoint. Returns the checkpoint id, or None when
        nothing was captured (disabled, non-git, or no edit targets given).
        """
        if not self._enabled:
            return None
        if not _is_git_repo(self._cwd):
            return None

        targets = self._normalise(paths)
        if not targets:
            self._report_inert_call(tool_name)
            return None

        self._report_legacy_stashes()
        self._prune_stale_sessions()

        edit_id       = self._next_edit_id()
        checkpoint_id = f"{edit_id:06d}"
        directory     = self._root / checkpoint_id

        try:
            records = _capture_paths(targets, directory)
            _write_manifest(directory, self._session_id, edit_id, records)
        except OSError as exc:
            logger.debug("checkpoint: capture failed before %s: %s", tool_name, exc)
            _remove_tree(directory)
            return None

        logger.debug("checkpoint: captured %s before %s", checkpoint_id, tool_name)
        self._clear_redo()
        self._evict_old_snapshots()
        return checkpoint_id

    def undo(self) -> str:
        """Restore the newest snapshot, touching only the files it captured.

        The current contents of those same files are captured first so redo can
        replay them. Returns a human-readable status message.
        """
        if not _is_git_repo(self._cwd):
            return "Not a git repository. Undo unavailable."

        snapshots = self._snapshots()
        if not snapshots:
            return "Nothing to undo."

        latest    = snapshots[-1]
        directory = self._root / latest.checkpoint_id
        records   = self._manifest_records(directory)
        if records is None:
            _remove_tree(directory)
            return f"Undo failed: checkpoint {latest.checkpoint_id} is unreadable."

        try:
            redo_dir            = self._store_redo(latest, records)
            restored, skipped   = _restore_records(records, directory)
        except OSError as exc:
            return f"Undo failed: {exc}"

        _remove_tree(directory)
        self._redo_stack.append(redo_dir)

        suffix = f", {skipped} skipped" if skipped else ""
        return (
            f"Undone checkpoint {latest.checkpoint_id} (edit #{latest.edit_id}): "
            f"{restored} file(s) restored{suffix}."
        )

    def redo(self) -> str:
        """Re-apply the edit that the last undo rolled back.

        The rolled-back state is captured as a fresh checkpoint first, so undo
        stays available after a redo. Returns a human-readable status message.
        """
        if not _is_git_repo(self._cwd):
            return "Not a git repository. Redo unavailable."

        while self._redo_stack:
            redo_dir = self._redo_stack.pop()
            records  = self._manifest_records(redo_dir)
            if records is None:
                _remove_tree(redo_dir)
                continue

            edit_id       = self._next_edit_id()
            checkpoint_id = f"{edit_id:06d}"
            directory     = self._root / checkpoint_id
            targets       = [str(r.get("path") or "") for r in records if r.get("path")]

            try:
                back = _capture_paths(targets, directory)
                _write_manifest(directory, self._session_id, edit_id, back)
                restored, skipped = _restore_records(records, redo_dir)
            except OSError as exc:
                _remove_tree(directory)
                return f"Redo failed: {exc}"

            _remove_tree(redo_dir)
            self._evict_old_snapshots()

            suffix = f", {skipped} skipped" if skipped else ""
            return (
                f"Redone edit as checkpoint {checkpoint_id}: "
                f"{restored} file(s) re-applied{suffix}."
            )

        return "Nothing to redo."

    def list_checkpoints(self) -> list[CheckpointEntry]:
        """Return this session's snapshots, oldest first.

        Leftover stashes from an earlier build are listed ahead of them so the
        work they hold stays visible instead of ageing out of sight.
        """
        if not _is_git_repo(self._cwd):
            return []
        return self._legacy_entries() + self._snapshots()

    def legacy_stashes(self) -> list[StashEntry]:
        """Return nerdvana stashes an earlier checkpoint build left behind."""
        return _list_all_stashes(self._cwd)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _normalise(self, paths: Sequence[str] | None) -> list[str]:
        """Absolute, de-duplicated, capped list of edit targets."""
        if not paths:
            return []

        base    = Path(self._cwd)
        ordered: dict[str, None] = {}
        for raw in paths:
            text = str(raw).strip()
            if not text:
                continue
            candidate = Path(text).expanduser()
            if not candidate.is_absolute():
                candidate = base / candidate
            ordered.setdefault(str(candidate), None)

        targets = list(ordered)
        if len(targets) > _MAX_FILES_PER_EDIT:
            logger.debug(
                "checkpoint: capturing %d of %d edit targets",
                _MAX_FILES_PER_EDIT,
                len(targets),
            )
            targets = targets[:_MAX_FILES_PER_EDIT]
        return targets

    def _snapshot_dirs(self) -> list[Path]:
        """Snapshot directories of this session, oldest first."""
        try:
            children = [
                child for child in self._root.iterdir()
                if child.is_dir() and child.name != _REDO_DIRNAME
            ]
        except OSError:
            return []
        return sorted(children, key=lambda child: child.name)

    def _snapshots(self) -> list[CheckpointEntry]:
        """Readable snapshots of this session, oldest first."""
        entries: list[CheckpointEntry] = []
        for directory in self._snapshot_dirs():
            manifest = _read_manifest(directory)
            if manifest is None:
                continue
            try:
                edit_id = int(manifest.get("edit_id", 0))
            except (TypeError, ValueError):
                continue
            records = manifest.get("entries")
            paths   = (
                tuple(str(record.get("path") or "") for record in records)
                if isinstance(records, list) else ()
            )
            entries.append(CheckpointEntry(
                checkpoint_id = directory.name,
                session       = str(manifest.get("session", self._session_id)),
                edit_id       = edit_id,
                paths         = paths,
            ))
        return entries

    def _legacy_entries(self) -> list[CheckpointEntry]:
        """Leftover stashes rendered as informational checkpoint rows."""
        return [
            CheckpointEntry(
                checkpoint_id = stash.stash_ref,
                session       = stash.session,
                edit_id       = stash.edit_id,
                paths         = (),
                kind          = _KIND_LEGACY_STASH,
            )
            for stash in self.legacy_stashes()
        ]

    def _manifest_records(self, directory: Path) -> list[dict[str, Any]] | None:
        """Return the captured records of *directory*, or None when unreadable."""
        manifest = _read_manifest(directory)
        if manifest is None:
            return None
        records = manifest.get("entries")
        if not isinstance(records, list):
            return None
        return [record for record in records if isinstance(record, dict)]

    def _next_edit_id(self) -> int:
        """Hand out the next monotonic edit id for this session."""
        if self._edit_counter is None:
            self._edit_counter = max(
                (entry.edit_id for entry in self._snapshots()),
                default=0,
            )
        self._edit_counter += 1
        return self._edit_counter

    def _store_redo(self, entry: CheckpointEntry, records: list[dict[str, Any]]) -> Path:
        """Capture the post-edit state of *entry*'s paths for a later redo."""
        redo_dir = self._root / _REDO_DIRNAME / entry.checkpoint_id
        _remove_tree(redo_dir)
        targets = [str(record.get("path") or "") for record in records if record.get("path")]
        captured = _capture_paths(targets, redo_dir)
        _write_manifest(redo_dir, self._session_id, entry.edit_id, captured)
        return redo_dir

    def _clear_redo(self) -> None:
        """Drop every pending redo snapshot: a new edit invalidates them."""
        self._redo_stack.clear()
        _remove_tree(self._root / _REDO_DIRNAME)

    def _evict_old_snapshots(self) -> None:
        """Drop the oldest snapshots once per_session_max is exceeded."""
        directories = self._snapshot_dirs()
        excess      = len(directories) - self._per_session_max
        if excess <= 0:
            return
        for directory in directories[:excess]:
            _remove_tree(directory)
            logger.debug("checkpoint: evicted %s (LRU)", directory.name)

    def _prune_stale_sessions(self) -> None:
        """Remove snapshot directories of sessions untouched for a week."""
        if self._pruned:
            return
        self._pruned = True

        cutoff = time.time() - _SESSION_TTL_SECS
        try:
            children = list(self._root.parent.iterdir())
        except OSError:
            return

        for child in children:
            if child == self._root or not child.is_dir():
                continue
            try:
                stale = child.stat().st_mtime < cutoff
            except OSError:
                continue
            if stale:
                _remove_tree(child)
                logger.debug("checkpoint: pruned stale session %s", child.name)

    def _report_inert_call(self, tool_name: str) -> None:
        """Report a checkpoint request that named no file to protect.

        Raised once per session rather than per call: the condition is a
        property of the caller, not of one edit, and repeating it every edit
        would train the reader to ignore it.
        """
        if self._inert_reported:
            return
        self._inert_reported = True
        logger.warning(
            "checkpoint: %s asked for a checkpoint without naming the files it edits, "
            "so nothing was captured and undo has nothing to restore. Undo stays inert "
            "until the tool executor passes its edit targets to before_edit().",
            tool_name,
        )

    def _report_legacy_stashes(self) -> None:
        """Tell the user about stashes an earlier build hid work in.

        These are left in place. A stash written by the previous build may be
        the only copy of work that was taken out of a working tree, so it is
        reported for recovery and never dropped automatically.
        """
        if self._legacy_checked:
            return
        self._legacy_checked = True

        leftovers = self.legacy_stashes()
        if not leftovers:
            return
        mine = _list_session_stashes(self._cwd, self._session_id)
        logger.warning(
            "checkpoint: %d stash entries written by an earlier build (%d from this session) "
            "may still hold uncommitted work. They are left untouched. Inspect one with "
            "'git stash show -p %s' and recover it with 'git stash apply %s'.",
            len(leftovers),
            len(mine),
            leftovers[0].stash_ref,
            leftovers[0].stash_ref,
        )
