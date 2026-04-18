"""Diff-preview CodeEditor for Phase D: ReplaceSymbolBody 2-step workflow.

Manages a session-scoped preview store (max 20, LRU eviction).
Each preview captures a WorkspaceEdit + per-file SHA256 fingerprints.
apply() re-validates SHA256 before writing; returns STALE if file changed.

작성자: 최진호
작성일: 2026-04-18
"""
from __future__ import annotations

import difflib
import hashlib
import os
import secrets
from collections import OrderedDict
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, NamedTuple

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

PreviewKind = Literal["replace_body", "insert_before", "insert_after", "delete", "rename"]


class PreviewEntry(NamedTuple):
    """Immutable snapshot of a pending edit operation.

    Attributes
    ----------
    preview_id:
        Hex token uniquely identifying this preview.
    workspace_edit:
        Raw LSP WorkspaceEdit dict (documentChanges or changes).
    target_files:
        Mapping of absolute file path → SHA256 hex at preview creation time.
    created_at:
        UTC timestamp of preview creation.
    kind:
        Edit operation kind.
    diff_text:
        Unified diff string shown to the caller.
    """

    preview_id:     str
    workspace_edit: dict[str, Any]
    target_files:   dict[str, str]      # path → sha256 hex
    created_at:     datetime
    kind:           PreviewKind
    diff_text:      str


# ---------------------------------------------------------------------------
# Public errors
# ---------------------------------------------------------------------------


class StalePreviewError(Exception):
    """Raised when a target file has changed since the preview was created."""

    def __init__(self, preview_id: str, changed_paths: list[str]) -> None:
        self.preview_id    = preview_id
        self.changed_paths = changed_paths
        super().__init__(
            f"Preview {preview_id!r} is stale: files changed since preview — "
            + ", ".join(changed_paths)
        )


class UnknownPreviewError(Exception):
    """Raised when the requested preview_id does not exist."""


# ---------------------------------------------------------------------------
# CodeEditor
# ---------------------------------------------------------------------------


class CodeEditor:
    """Session-scoped diff-preview manager.

    Workflow:
        1. Call :meth:`create_preview` with the edit kind + workspace_edit.
           Returns (preview_id, diff_text).
        2. Call :meth:`apply` with the preview_id to commit the changes.
           Validates per-file SHA256; raises :exc:`StalePreviewError` if
           any target file has been modified since step 1.

    Parameters
    ----------
    project_root:
        Workspace root; used to resolve relative paths and for safe_open_fd.
    lru_max:
        Maximum number of concurrent previews. Oldest is evicted on overflow.
        Defaults to 20; can be overridden via ``.nerdvana.yml``
        ``preview.lru_max``.
    """

    DEFAULT_LRU_MAX = 20

    def __init__(
        self,
        project_root: str | None = None,
        lru_max:      int        = DEFAULT_LRU_MAX,
    ) -> None:
        self._project_root = project_root or os.getcwd()
        self._lru_max      = lru_max
        # OrderedDict preserves insertion order; we move accessed items to end
        self._store: OrderedDict[str, PreviewEntry] = OrderedDict()

    # -- public API --

    def create_preview(
        self,
        kind:           PreviewKind,
        workspace_edit: dict[str, Any],
        new_contents:   dict[str, str],   # abs_path → proposed new content
    ) -> tuple[str, str]:
        """Build a unified diff and store the preview.

        Parameters
        ----------
        kind:
            Edit operation kind.
        workspace_edit:
            Raw LSP WorkspaceEdit to be applied on commit.
        new_contents:
            Mapping of absolute path → new full file content (after edit).
            Used only for diff generation; the actual write uses workspace_edit.

        Returns
        -------
        (preview_id, diff_text)
        """
        target_files: dict[str, str] = {}
        diff_lines:   list[str]      = []

        for abs_path, new_text in new_contents.items():
            # Read current file content (may not exist for new files)
            try:
                with open(abs_path, encoding="utf-8") as fh:
                    original = fh.read()
            except FileNotFoundError:
                original = ""

            target_files[abs_path] = _sha256(original.encode())

            # Unified diff
            rel = _rel_path(abs_path, self._project_root)
            diff = list(difflib.unified_diff(
                original.splitlines(keepends=True),
                new_text.splitlines(keepends=True),
                fromfile=f"a/{rel}",
                tofile=f"b/{rel}",
            ))
            diff_lines.extend(diff)

        diff_text  = "".join(diff_lines) or "(no changes)"
        preview_id = _gen_preview_id()

        entry = PreviewEntry(
            preview_id     = preview_id,
            workspace_edit = workspace_edit,
            target_files   = target_files,
            created_at     = datetime.utcnow(),
            kind           = kind,
            diff_text      = diff_text,
        )
        self._store[preview_id] = entry
        self.evict_lru()
        return preview_id, diff_text

    def apply(self, preview_id: str) -> dict[str, Any]:
        """Apply a previously created preview.

        Returns
        -------
        ``{"status": "applied", "changed_files": [...]}`` on success.

        Raises
        ------
        UnknownPreviewError:
            preview_id not found in store.
        StalePreviewError:
            One or more target files changed since the preview was created.
        """
        entry = self._store.get(preview_id)
        if entry is None:
            raise UnknownPreviewError(f"No preview with id={preview_id!r}")

        # SHA256 validation
        stale_paths: list[str] = []
        for abs_path, recorded_sha in entry.target_files.items():
            current_sha = _current_sha256(abs_path)
            if current_sha != recorded_sha:
                stale_paths.append(abs_path)

        if stale_paths:
            raise StalePreviewError(preview_id, stale_paths)

        # Apply workspace edit
        from nerdvana_cli.core.lsp_client import _apply_workspace_edit  # noqa: PLC0415
        result = _apply_workspace_edit(
            entry.workspace_edit,
            cwd=self._project_root,
        )

        # Remove from store (consumed)
        self._store.pop(preview_id, None)

        return {"status": "applied", "changed_files": result.get("changed_files", [])}

    def get(self, preview_id: str) -> PreviewEntry | None:
        """Return a preview entry and touch it (LRU refresh)."""
        entry = self._store.get(preview_id)
        if entry is not None:
            # Move to end (most-recently-used)
            self._store.move_to_end(preview_id)
        return entry

    def evict_lru(self, max_count: int | None = None) -> int:
        """Evict oldest entries until store size ≤ max_count.

        Returns
        -------
        Number of entries evicted.
        """
        limit   = max_count if max_count is not None else self._lru_max
        evicted = 0
        while len(self._store) > limit:
            self._store.popitem(last=False)   # remove oldest (front)
            evicted += 1
        return evicted

    def pending_count(self) -> int:
        """Return number of previews currently in store."""
        return len(self._store)

    def discard(self, preview_id: str) -> bool:
        """Remove a specific preview without applying it."""
        if preview_id in self._store:
            del self._store[preview_id]
            return True
        return False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _current_sha256(abs_path: str) -> str:
    """Read file and return SHA256; return empty string if unreadable."""
    try:
        with open(abs_path, "rb") as fh:
            return _sha256(fh.read())
    except OSError:
        return ""


def _gen_preview_id(length: int = 12) -> str:
    """Generate a cryptographically random hex preview ID."""
    return secrets.token_hex(length // 2)


def _rel_path(abs_path: str, project_root: str) -> str:
    """Return relative path string for display; fall back to basename."""
    try:
        return os.path.relpath(abs_path, project_root)
    except ValueError:
        return Path(abs_path).name
