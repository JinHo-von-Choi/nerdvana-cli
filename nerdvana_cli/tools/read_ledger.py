"""Per-session ledger of file digests recorded when a file is read.

The file tools use it to refuse edits and overwrites against a file whose
content moved on after the model last saw it.  Entries are keyed by session id
and then by resolved path, live at module level so they outlast the
``ToolContext`` created for each user turn, and never cross between sessions.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

import hashlib
import os
import threading
from typing import Any

_DEFAULT_SESSION: str = "default"

_ledgers: dict[str, dict[str, str]] = {}
_ranges: dict[str, dict[str, list[tuple[int, int, str]]]] = {}
_lock = threading.Lock()


def content_digest(data: bytes) -> str:
    """Return the sha256 hex digest of the raw bytes of a whole file."""
    return hashlib.sha256(data).hexdigest()


def session_key(context: Any) -> str:
    """Return the ledger key for ``context``: its session id, else ``default``."""
    state = getattr(context, "state", None)
    if isinstance(state, dict):
        raw = state.get("session_id")
        if raw:
            return str(raw)
    return _DEFAULT_SESSION


def resolve_key(relative_path: str, cwd: str) -> str:
    """Return the canonical absolute path used to index the ledger."""
    return os.path.realpath(os.path.join(cwd, relative_path))


def record(session: str, path_key: str, digest: str) -> None:
    """Store ``digest`` as the last known content of ``path_key`` for ``session``."""
    with _lock:
        _ledgers.setdefault(session, {})[path_key] = digest


def lookup(session: str, path_key: str) -> str | None:
    """Return the recorded digest for ``path_key`` in ``session``, or None."""
    with _lock:
        return _ledgers.get(session, {}).get(path_key)


def range_digest(lines: list[str]) -> str:
    """The digest of a run of lines, newlines included, as the file holds them."""
    return content_digest("".join(lines).encode("utf-8", errors="surrogateescape"))


def record_range(session: str, path_key: str, start: int, end: int, digest: str) -> None:
    """Remember that lines ``start``..``end`` (1-based, inclusive) of a file were shown with this digest.

    A symbol read shows part of a file. The part can be edited later without reading the whole file,
    as long as those lines still have this digest.
    """
    with _lock:
        entries = _ranges.setdefault(session, {}).setdefault(path_key, [])
        entries[:] = [e for e in entries if e[:2] != (start, end)]
        entries.append((start, end, digest))


def lookup_ranges(session: str, path_key: str) -> list[tuple[int, int, str]]:
    """The recorded ranges of a file: ``(start, end, digest)`` triples."""
    with _lock:
        return list(_ranges.get(session, {}).get(path_key, ()))


def drop_ranges(session: str, path_key: str) -> None:
    """Forget the ranges of a file, as after an edit that moved its lines."""
    with _lock:
        _ranges.get(session, {}).pop(path_key, None)


def clear(session: str | None = None) -> None:
    """Drop the ledger of one session, or every ledger when ``session`` is None."""
    with _lock:
        if session is None:
            _ledgers.clear()
            _ranges.clear()
        else:
            _ledgers.pop(session, None)
            _ranges.pop(session, None)
