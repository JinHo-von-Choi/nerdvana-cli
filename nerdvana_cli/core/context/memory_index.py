"""Sidecar metadata for the file-backed memory scopes.

Every scope directory keeps one ``.index.json`` next to its ``.md`` files. It
records, per memory name, when the entry was created and last modified, where it
came from (user, agent or import) and when it was last read. The memory files stay
plain text: ``ReadMemory`` returns exactly what was written.

The recorded modification time is trusted while the file still holds the content
it was recorded for. A file that changed behind the index (an editor, a merge)
reports its own mtime instead, and a fresh checkout, which resets every mtime to
a later time, keeps the recorded times because the content hash still matches.

Concurrency: every change is a read-modify-write under an exclusive flock on
``.index.lock`` and lands through an atomic rename.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import logging
import os
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

INDEX_NAME = ".index.json"
LOCK_NAME  = ".index.lock"

# A file whose mtime is within this many seconds of the recorded time counts as unchanged.
_MTIME_SLACK = 1.0


class MemorySource(StrEnum):
    """Where a memory entry came from."""

    USER    = "user"
    AGENT   = "agent"
    IMPORT  = "import"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class MemoryMeta:
    """Recorded history of one memory entry (Unix timestamps in seconds)."""

    created:   float
    modified:  float
    source:    str
    sha256:    str
    loaded_at: float | None = None


def content_hash(content: str) -> str:
    """Hex SHA-256 of *content* as UTF-8."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def index_key(name: str) -> str:
    """The index key of a memory name: the name without its optional ``.md`` suffix."""
    return name[: -len(".md")] if name.endswith(".md") else name


def effective_modified(meta: MemoryMeta | None, path: Path, mtime: float) -> float:
    """Last-modified time of the file at *path* whose filesystem mtime is *mtime*.

    The recorded time wins while the file looks untouched. An mtime older than the record
    is taken as given (the file was restored with its own times), and a newer one counts
    only when the content differs from what was recorded.
    """
    if meta is None or mtime < meta.modified - _MTIME_SLACK:
        return mtime
    if mtime <= meta.modified + _MTIME_SLACK:
        return meta.modified
    try:
        current = content_hash(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError):
        return mtime
    return meta.modified if current == meta.sha256 else mtime


class MemoryIndex:
    """The ``.index.json`` of one scope directory."""

    def __init__(self, base_dir: Path) -> None:
        self._base = base_dir

    def records(self) -> dict[str, MemoryMeta]:
        """Every recorded entry. A missing or unreadable index reads as empty."""
        path = self._base / INDEX_NAME
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            return {name: MemoryMeta(**fields) for name, fields in raw.items()}
        except FileNotFoundError:
            return {}
        except (OSError, ValueError, TypeError, AttributeError) as exc:
            logger.warning("Ignoring unreadable memory index %s: %s", path, exc)
            return {}

    def get(self, name: str) -> MemoryMeta | None:
        """The record of *name*, or None when none was made."""
        return self.records().get(index_key(name))

    def record_write(
        self,
        name:    str,
        content: str,
        source:  str | None       = None,
        created: float | None     = None,
    ) -> None:
        """Record that *name* now holds *content*.

        A known entry keeps its creation time and, when *source* is None, its source.
        *created* overrides the creation time (a rename carries the old one over).
        """
        key = index_key(name)

        def apply(records: dict[str, MemoryMeta]) -> None:
            now = time.time()
            old = records.get(key)
            records[key] = MemoryMeta(
                created   = created if created is not None else (old.created if old else now),
                modified  = now,
                source    = source or (old.source if old else MemorySource.UNKNOWN.value),
                sha256    = content_hash(content),
                loaded_at = old.loaded_at if old else None,
            )

        self._update(apply)

    def record_load(self, name: str, content: str, mtime: float) -> None:
        """Record that *name*, which holds *content* and has filesystem mtime *mtime*, was just read.

        An entry written before the index existed gets a record dated by its mtime.
        """
        key = index_key(name)

        def apply(records: dict[str, MemoryMeta]) -> None:
            old = records.get(key) or MemoryMeta(
                created  = mtime,
                modified = mtime,
                source   = MemorySource.UNKNOWN.value,
                sha256   = content_hash(content),
            )
            records[key] = MemoryMeta(**{**asdict(old), "loaded_at": time.time()})

        self._update(apply)

    def drop(self, name: str) -> None:
        """Forget the record of *name*."""
        self._update(lambda records: records.pop(index_key(name), None))

    def _update(self, change: Callable[[dict[str, MemoryMeta]], Any]) -> None:
        """Apply *change* to the records under the lock and store the result atomically."""
        self._base.mkdir(parents=True, exist_ok=True)
        with open(self._base / LOCK_NAME, "a", encoding="utf-8") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                records = self.records()
                change(records)
                tmp = self._base / f"{INDEX_NAME}.tmp"
                tmp.write_text(
                    json.dumps({n: asdict(m) for n, m in sorted(records.items())}, indent=1),
                    encoding="utf-8",
                )
                os.replace(tmp, self._base / INDEX_NAME)
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)
