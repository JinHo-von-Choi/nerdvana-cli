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

Each file-backed scope also keeps a sidecar index (core/context/memory_index.py) with the
created and last-modified times, the source and the last load of every entry.

Author: 최진호
Date:   2026-04-18
"""

from __future__ import annotations

import fcntl
import logging
import os
import re
import time
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import IO

from nerdvana_cli.core.config import paths as core_paths
from nerdvana_cli.core.context.memory_index import MemoryIndex, MemorySource, effective_modified
from nerdvana_cli.utils.path import validate_path

logger = logging.getLogger(__name__)

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

    name:       str
    scope:      MemoryScope
    size:       int
    mtime:      float  # Unix timestamp of the last modification
    importance: float = 0.5
    created:    float = 0.0  # Unix timestamp of creation
    source:     str   = MemorySource.UNKNOWN.value
    loaded_at:  float | None = None  # Unix timestamp of the last read; None when never read


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SAFE_NAME     = re.compile(r"^[A-Za-z0-9_./-]+$")
_DOTDOT_GUARD  = re.compile(r"(^|/)\.\.(/|$)")
_ABSOLUTE_NAME = re.compile(r"^(?:[/\\]|[A-Za-z]:)")

# Largest memory name and entry accepted. Memories are short notes, not file storage.
MAX_NAME_LENGTH   = 200
MAX_CONTENT_BYTES = 64 * 1024

# Scopes backed by a directory of ``.md`` files, in lookup order.
_FILE_SCOPES = (MemoryScope.PROJECT_KNOWLEDGE, MemoryScope.USER_GLOBAL)


def _validate_name(name: str) -> None:
    """Raise ValueError if *name* cannot serve as a relative memory path.

    This inspects the raw string only and is a fast reject, not the boundary.
    A name that passes here may still resolve outside its base directory
    through a symlink, so :func:`_memory_path` re-checks the joined path.
    """
    if not name:
        raise ValueError("Memory name must not be empty.")
    if len(name) > MAX_NAME_LENGTH:
        raise ValueError(f"Memory name is too long ({len(name)} characters, limit {MAX_NAME_LENGTH}).")
    if _ABSOLUTE_NAME.match(name):
        raise ValueError(
            f"Memory name {name!r} is invalid: absolute paths, UNC shares and "
            "drive-letter prefixes are not allowed."
        )
    if "%" in name:
        raise ValueError(
            f"Memory name {name!r} is invalid: percent-encoded characters are not allowed."
        )
    if not _SAFE_NAME.match(name):
        raise ValueError(
            f"Memory name {name!r} is invalid. "
            "Use only letters, digits, dot, underscore, hyphen, and slash."
        )
    if _DOTDOT_GUARD.search(name):
        raise ValueError(
            f"Memory name {name!r} is invalid: '..' path traversal is not allowed."
        )
    if any(not part or part.startswith(".") for part in name.split("/")):
        raise ValueError(
            f"Memory name {name!r} is invalid: empty and dot-prefixed path segments are not allowed."
        )


def _check_content(content: str) -> None:
    """Raise ValueError if *content* is larger than one memory entry may be."""
    size = len(content.encode("utf-8"))
    if size > MAX_CONTENT_BYTES:
        raise ValueError(f"Memory content is too large ({size} bytes, limit {MAX_CONTENT_BYTES}).")


def _check_rule_name(name: str) -> None:
    """Raise ValueError if *name* cannot head a section of NIRNA.md (one line, bounded length)."""
    if not name.strip() or "\n" in name or "\r" in name or len(name) > MAX_NAME_LENGTH:
        raise ValueError(
            f"Rule name is invalid: it must be a single line of at most {MAX_NAME_LENGTH} characters."
        )


def _memory_path(base_dir: Path, name: str) -> Path:
    """Resolve *name* (possibly slash-namespaced) to a path inside *base_dir*.

    Args:
        base_dir: Scope root that the returned path must stay under.
        name:     Memory name, optionally slash-namespaced.

    Returns:
        The absolute path of the backing ``.md`` file.

    Raises:
        ValueError: *name* is malformed, or the joined path resolves outside
            *base_dir*. The second case covers symlinked components, which
            no amount of string inspection can detect.
    """
    _validate_name(name)
    filename = name if name.endswith(".md") else name + ".md"
    escape = validate_path(filename, str(base_dir))
    if escape is not None:
        raise ValueError(f"Memory name {name!r} is invalid: {escape}")
    return base_dir / filename


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

    def _index_for(self, scope: MemoryScope) -> MemoryIndex:
        """The sidecar index of a file-backed *scope*."""
        base_dir = self._base_dir_for(scope)
        assert base_dir is not None
        return MemoryIndex(base_dir)

    def _locate(self, name: str, scope: MemoryScope | None = None) -> tuple[MemoryScope, Path] | None:
        """Scope and path of the existing memory *name* (in *scope* only, when given), or None."""
        for candidate in (scope,) if scope is not None else _FILE_SCOPES:
            base_dir = self._base_dir_for(candidate)
            assert base_dir is not None
            path = _memory_path(base_dir, name)
            if path.exists():
                return candidate, path
        return None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def validate_write(self, name: str, content: str, scope: MemoryScope) -> None:
        """Raise unless *content* may be stored as *name* under *scope*.

        Raises NotImplementedError for AGENT_EXPERIENCE and ValueError for a malformed
        or escaping name or for oversized content.
        """
        if scope == MemoryScope.AGENT_EXPERIENCE:
            raise NotImplementedError(
                "AGENT_EXPERIENCE memories are managed by AnchorMind. "
                "Run: mcp__anchormind__remember  (or use the AnchorMind CLI)."
            )
        _check_content(content)
        if scope == MemoryScope.PROJECT_RULE:
            _check_rule_name(name)
            return
        base_dir = self._base_dir_for(scope)
        assert base_dir is not None
        _memory_path(base_dir, name)

    def write(
        self,
        name:    str,
        content: str,
        scope:   MemoryScope,
        source:  MemorySource = MemorySource.USER,
    ) -> str:
        """Write *content* to the memory identified by *name* under *scope*.

        *source* is recorded as the entry's origin. Returns a human-readable
        confirmation string. Raises NotImplementedError for AGENT_EXPERIENCE and
        ValueError for an invalid name or oversized content.
        """
        self.validate_write(name, content, scope)
        if scope == MemoryScope.PROJECT_RULE:
            return self._append_to_nirnamd(name, content)

        base_dir = self._base_dir_for(scope)
        assert base_dir is not None
        path = _memory_path(base_dir, name)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "r+" if path.exists() else "w", encoding="utf-8") as fp:
            _locked_write(fp, content)
        self._index_for(scope).record_write(name, content, source=source.value)
        return f"Wrote memory '{name}' [{scope}] ({len(content)} bytes) → {path}"

    def scope_of(self, name: str) -> MemoryScope | None:
        """The first file-backed scope that holds memory *name*, or None."""
        found = self._locate(name)
        return found[0] if found else None

    def peek(self, name: str, scope: MemoryScope) -> str | None:
        """Content of memory *name* in *scope* without recording a load, or None when absent."""
        found = self._locate(name, scope)
        if found is None:
            return None
        with open(found[1], encoding="utf-8") as fp:
            return _locked_read(fp)

    def read(self, name: str) -> str:
        """Return the content of memory *name*, searching all file-backed scopes.

        Search order: PROJECT_KNOWLEDGE → USER_GLOBAL. The read is recorded as
        the entry's last load. Raises FileNotFoundError if not found in any scope.
        """
        found = self._locate(name)
        if found is None:
            raise FileNotFoundError(f"Memory '{name}' not found in project or global scope.")
        scope, path = found
        with open(path, encoding="utf-8") as fp:
            content = _locked_read(fp)
        try:
            self._index_for(scope).record_load(name, content, path.stat().st_mtime)
        except OSError as exc:
            logger.warning("Could not record the load of memory %r: %s", name, exc)
        return content

    def delete(self, name: str, scope: MemoryScope | None = None) -> str:
        """Delete memory *name* from *scope*, or from the first file-backed scope that has it.

        Returns a confirmation string. Raises FileNotFoundError if absent.
        """
        found = self._locate(name, scope)
        if found is None:
            raise FileNotFoundError(f"Memory '{name}' not found.")
        found_scope, path = found
        os.remove(path)
        self._index_for(found_scope).drop(name)
        return f"Deleted memory '{name}' [{found_scope}] from {path}"

    def rename(self, old_name: str, new_name: str, new_scope: MemoryScope | None = None) -> str:
        """Rename *old_name* to *new_name*, optionally changing scope.

        For cross-scope rename: reads old, writes to new scope, deletes old. The entry
        keeps its creation time and source.
        Raises FileNotFoundError if source is absent.
        Raises ValueError if source or destination name is invalid.
        """
        _validate_name(new_name)

        found = self._locate(old_name)
        if found is None:
            raise FileNotFoundError(f"Memory '{old_name}' not found.")
        src_scope, src_path = found

        with open(src_path, encoding="utf-8") as fp:
            content = _locked_read(fp)
        prior        = self._index_for(src_scope).get(old_name)
        source       = MemorySource(prior.source) if prior else MemorySource.UNKNOWN
        target_scope = new_scope if new_scope is not None else src_scope
        self.write(new_name, content, target_scope, source=source)
        if prior is not None:
            self._index_for(target_scope).record_write(new_name, content, created=prior.created)
        os.remove(src_path)
        self._index_for(src_scope).drop(old_name)
        return f"Renamed '{old_name}' → '{new_name}' [{target_scope}]"

    def preview_edit(
        self,
        name:   str,
        needle: str,
        repl:   str,
        mode:   str = "literal",
    ) -> tuple[MemoryScope, str, int]:
        """Apply the edit of :meth:`edit` to a copy: ``(scope, new content, replacement count)``.

        Raises FileNotFoundError if *name* is not found and ValueError if *mode* is invalid.
        """
        if mode not in ("literal", "regex"):
            raise ValueError(f"mode must be 'literal' or 'regex', got {mode!r}")
        found = self._locate(name)
        if found is None:
            raise FileNotFoundError(f"Memory '{name}' not found.")
        scope, path = found
        with open(path, encoding="utf-8") as fp:
            original = _locked_read(fp)
        if mode == "literal":
            return scope, original.replace(needle, repl), original.count(needle)
        return scope, re.sub(needle, repl, original), len(re.findall(needle, original))

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
        Raises ValueError if *mode* is invalid or the result exceeds the size limit.
        """
        scope, updated, count = self.preview_edit(name, needle, repl, mode)
        _check_content(updated)
        found = self._locate(name, scope)
        assert found is not None
        with open(found[1], "r+", encoding="utf-8") as fp:
            _locked_write(fp, updated)
        self._index_for(scope).record_write(name, updated)
        return f"Edited '{name}': {count} replacement(s) applied."

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
        for scope in _FILE_SCOPES:
            base_dir = self._base_dir_for(scope)
            assert base_dir is not None
            if not base_dir.exists():
                continue
            records = self._index_for(scope).records()
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
                    meta = records.get(display)
                    entry = MemoryEntry(
                        name      = display,
                        scope     = scope,
                        size      = stat.st_size,
                        mtime     = effective_modified(meta, fpath, stat.st_mtime),
                        created   = meta.created if meta else stat.st_mtime,
                        source    = meta.source if meta else MemorySource.UNKNOWN.value,
                        loaded_at = meta.loaded_at if meta else None,
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
        never_loaded: bool = False,
    ) -> list[MemoryEntry]:
        """Return memories not modified in specified days.

        Args:
            days: Number of days since last modification.
            topic: Optional slash-namespace prefix filter.
            never_loaded: Keep only entries that no read has ever recorded.
        """
        cutoff = time.time() - (days * 86400)
        entries = self.list_memories(topic=topic)
        return [e for e in entries if e.mtime < cutoff and (e.loaded_at is None or not never_loaded)]
