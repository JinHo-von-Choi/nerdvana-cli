"""MemoriesManager — 4-scope project-local knowledge store.

Scopes
------
PROJECT_RULE       → appended to <cwd>/NIRNA.md  (append-mode document)
PROJECT_KNOWLEDGE  → <cwd>/.nerdvana/memories/
USER_GLOBAL        → ~/.nerdvana/memories/global/
AGENT_EXPERIENCE   → stub; delegates to AnchorMind CLI (not managed here)

Slash namespaces (e.g. "auth/login/rules") are stored as sub-directories.
Files are plain-text with a .md extension.

Concurrency: each read/write acquires an exclusive fcntl.flock on the file.

Author: 최진호
Date:   2026-04-18
"""

from __future__ import annotations

import fcntl
import os
import re
import time
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import IO

from nerdvana_cli.core import paths as core_paths

# ---------------------------------------------------------------------------
# Scope enum
# ---------------------------------------------------------------------------

class MemoryScope(StrEnum):
    PROJECT_RULE      = "project_rule"
    PROJECT_KNOWLEDGE = "project_knowledge"
    USER_GLOBAL       = "user_global"
    AGENT_EXPERIENCE  = "agent_experience"


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class MemoryEntry:
    """A single memory record returned by list."""

    name:  str
    scope: MemoryScope
    size:  int
    mtime: float
    importance: float = 0.5  # Unix timestamp


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SAFE_NAME    = re.compile(r"^[A-Za-z0-9_./-]+$")
_DOTDOT_GUARD = re.compile(r"(^|/)\.\.(/|$)")


def _validate_name(name: str) -> None:
    """Raise ValueError if *name* contains unsafe characters or path traversal."""
    if not name or not _SAFE_NAME.match(name):
        raise ValueError(
            f"Memory name {name!r} is invalid. "
            "Use only letters, digits, dot, underscore, hyphen, and slash."
        )
    if _DOTDOT_GUARD.search(name):
        raise ValueError(
            f"Memory name {name!r} is invalid: '..' path traversal is not allowed."
        )


def _memory_path(base_dir: Path, name: str) -> Path:
    """Resolve *name* (possibly slash-namespaced) to an absolute Path."""
    _validate_name(name)
    if not name.endswith(".md"):
        name = name + ".md"
    return base_dir / name


def _locked_read(fp: IO[str]) -> str:
    """Read file contents while holding an exclusive lock."""
    fcntl.flock(fp, fcntl.LOCK_EX)
    try:
        fp.seek(0)
        return fp.read()
    finally:
        fcntl.flock(fp, fcntl.LOCK_UN)


def _locked_write(fp: IO[str], content: str) -> None:
    """Write *content* to *fp* while holding an exclusive lock."""
    fcntl.flock(fp, fcntl.LOCK_EX)
    try:
        fp.seek(0)
        fp.truncate()
        fp.write(content)
        fp.flush()
    finally:
        fcntl.flock(fp, fcntl.LOCK_UN)


# ---------------------------------------------------------------------------
# MemoriesManager
# ---------------------------------------------------------------------------

class MemoriesManager:
    """Manage memories across four scopes.

    Args:
        cwd: Current working directory (project root).
    """

    def __init__(self, cwd: str = ".") -> None:
        self._cwd = cwd

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _project_dir(self) -> Path:
        return core_paths.project_memories_dir(self._cwd)

    def _global_dir(self) -> Path:
        return core_paths.global_memories_dir()

    def _nirnamd_path(self) -> Path:
        return core_paths.project_nirnamd_path(self._cwd)

    def _base_dir_for(self, scope: MemoryScope) -> Path | None:
        """Return the base directory for *scope*, or None for AGENT_EXPERIENCE."""
        if scope == MemoryScope.PROJECT_KNOWLEDGE:
            return self._project_dir()
        if scope == MemoryScope.USER_GLOBAL:
            return self._global_dir()
        return None  # PROJECT_RULE and AGENT_EXPERIENCE handled separately

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def write(self, name: str, content: str, scope: MemoryScope) -> str:
        """Write *content* to the memory identified by *name* under *scope*.

        Returns a human-readable confirmation string.
        Raises NotImplementedError for AGENT_EXPERIENCE.
        """
        if scope == MemoryScope.AGENT_EXPERIENCE:
            raise NotImplementedError(
                "AGENT_EXPERIENCE memories are managed by AnchorMind. "
                "Run: mcp__anchormind__remember  (or use the AnchorMind CLI)."
            )

        if scope == MemoryScope.PROJECT_RULE:
            return self._append_to_nirnamd(name, content)

        base_dir = self._base_dir_for(scope)
        assert base_dir is not None
        path = _memory_path(base_dir, name)
        path.parent.mkdir(parents=True, exist_ok=True)
        mode = "r+" if path.exists() else "w"
        if mode == "r+":
            with open(path, mode, encoding="utf-8") as fp:
                _locked_write(fp, content)
        else:
            with open(path, "w", encoding="utf-8") as fp:
                _locked_write(fp, content)
        return f"Wrote memory '{name}' [{scope}] ({len(content)} bytes) → {path}"

    def read(self, name: str) -> str:
        """Return the content of memory *name*, searching all file-backed scopes.

        Search order: PROJECT_KNOWLEDGE → USER_GLOBAL.
        Raises FileNotFoundError if not found in any scope.
        """
        for scope in (MemoryScope.PROJECT_KNOWLEDGE, MemoryScope.USER_GLOBAL):
            base_dir = self._base_dir_for(scope)
            assert base_dir is not None
            path = _memory_path(base_dir, name)
            if path.exists():
                with open(path, encoding="utf-8") as fp:
                    return _locked_read(fp)
        raise FileNotFoundError(f"Memory '{name}' not found in project or global scope.")

    def delete(self, name: str) -> str:
        """Delete memory *name* from any file-backed scope.

        Returns a confirmation string. Raises FileNotFoundError if absent.
        """
        for scope in (MemoryScope.PROJECT_KNOWLEDGE, MemoryScope.USER_GLOBAL):
            base_dir = self._base_dir_for(scope)
            assert base_dir is not None
            path = _memory_path(base_dir, name)
            if path.exists():
                os.remove(path)
                return f"Deleted memory '{name}' [{scope}] from {path}"
        raise FileNotFoundError(f"Memory '{name}' not found.")

    def rename(self, old_name: str, new_name: str, new_scope: MemoryScope | None = None) -> str:
        """Rename *old_name* to *new_name*, optionally changing scope.

        For cross-scope rename: reads old, writes to new scope, deletes old.
        Raises FileNotFoundError if source is absent.
        Raises ValueError if source or destination name is invalid.
        """
        _validate_name(new_name)

        # Locate source in file-backed scopes
        src_scope: MemoryScope | None = None
        src_path:  Path | None        = None
        for scope in (MemoryScope.PROJECT_KNOWLEDGE, MemoryScope.USER_GLOBAL):
            base_dir = self._base_dir_for(scope)
            assert base_dir is not None
            path = _memory_path(base_dir, old_name)
            if path.exists():
                src_scope = scope
                src_path  = path
                break

        if src_scope is None or src_path is None:
            raise FileNotFoundError(f"Memory '{old_name}' not found.")

        content = self.read(old_name)
        target_scope = new_scope if new_scope is not None else src_scope
        self.write(new_name, content, target_scope)
        os.remove(src_path)
        return f"Renamed '{old_name}' → '{new_name}' [{target_scope}]"

    def edit(
        self,
        name:   str,
        needle: str,
        repl:   str,
        mode:   str = "literal",  # "literal" | "regex"
    ) -> str:
        """In-place search-and-replace within an existing memory.

        Args:
            name:   Memory name (as used in read/write).
            needle: Pattern to find (literal string or regex depending on *mode*).
            repl:   Replacement string.
            mode:   "literal" for exact match, "regex" for re.sub.

        Returns a confirmation string.
        Raises FileNotFoundError if *name* is not found.
        Raises ValueError if *mode* is invalid.
        """
        if mode not in ("literal", "regex"):
            raise ValueError(f"mode must be 'literal' or 'regex', got {mode!r}")

        for scope in (MemoryScope.PROJECT_KNOWLEDGE, MemoryScope.USER_GLOBAL):
            base_dir = self._base_dir_for(scope)
            assert base_dir is not None
            path = _memory_path(base_dir, name)
            if path.exists():
                with open(path, "r+", encoding="utf-8") as fp:
                    original = _locked_read(fp)
                    updated  = original.replace(needle, repl) if mode == "literal" else re.sub(needle, repl, original)
                    _locked_write(fp, updated)
                count = original.count(needle) if mode == "literal" else len(re.findall(needle, original))
                return f"Edited '{name}': {count} replacement(s) applied."

        raise FileNotFoundError(f"Memory '{name}' not found.")

    def list_memories(
        self,
        topic: str | None = None,
        min_importance: float = 0.0,
    ) -> list[MemoryEntry]:
        """Return MemoryEntry list for all file-backed memories.

        Args:
            topic: Optional slash-namespace prefix filter (e.g. "auth/login").
            min_importance: Minimum importance threshold (0.0-1.0).
        """
        entries: list[MemoryEntry] = []
        for scope in (MemoryScope.PROJECT_KNOWLEDGE, MemoryScope.USER_GLOBAL):
            base_dir = self._base_dir_for(scope)
            assert base_dir is not None
            if not base_dir.exists():
                continue
            for root, _, files in os.walk(base_dir):
                for fname in files:
                    if not fname.endswith(".md"):
                        continue
                    fpath   = Path(root) / fname
                    rel     = fpath.relative_to(base_dir)
                    display = str(rel)[: -len(".md")]
                    if topic and not display.startswith(topic):
                        continue
                    stat = fpath.stat()
                    entry = MemoryEntry(
                        name  = display,
                        scope = scope,
                        size  = stat.st_size,
                        mtime = stat.st_mtime,
                    )
                    if entry.importance >= min_importance:
                        entries.append(entry)
        return sorted(entries, key=lambda e: e.name)

    # ------------------------------------------------------------------
    # PROJECT_RULE — append to NIRNA.md
    # ------------------------------------------------------------------

    def _append_to_nirnamd(self, section_name: str, content: str) -> str:
        """Append a named section to <cwd>/NIRNA.md (creates file if absent)."""
        nirnamd = self._nirnamd_path()
        header  = f"\n\n## [Memory] {section_name}\n\n"
        with open(nirnamd, "a", encoding="utf-8") as fp:
            fcntl.flock(fp, fcntl.LOCK_EX)
            try:
                fp.write(header + content + "\n")
                fp.flush()
            finally:
                fcntl.flock(fp, fcntl.LOCK_UN)
        return f"Appended rule '{section_name}' to NIRNA.md ({len(content)} bytes)"

    # ------------------------------------------------------------------
    # Onboarding helpers
    # ------------------------------------------------------------------

    def onboarding_exists(self) -> bool:
        """Return True if the onboarding stamp exists for the current project."""
        return core_paths.project_onboarding_dir(self._cwd).exists()

    def mark_onboarding_done(self) -> None:
        """Create the onboarding stamp directory."""
        core_paths.project_onboarding_dir(self._cwd).mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Session-start hint
    # ------------------------------------------------------------------

    def session_start_hint(self) -> str:
        """Return a short hint string for the system prompt.

        Lists the count of available project memories without loading content
        (prevents token over-consumption on every turn).
        """
        count = len(self.list_memories())
        if count == 0:
            return ""
        return (
            f"{count} project memor{'y' if count == 1 else 'ies'} available. "
            "Call ListMemories to see them."
        )

    def list_stale(
        self,
        days: int = 30,
        topic: str | None = None,
    ) -> list[MemoryEntry]:
        """Return memories not modified in specified days.

        Args:
            days: Number of days since last modification.
            topic: Optional slash-namespace prefix filter.
        """
        cutoff = time.time() - (days * 86400)
        entries = self.list_memories(topic=topic)
        return [e for e in entries if e.mtime < cutoff]
