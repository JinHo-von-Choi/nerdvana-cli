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


def clear(session: str | None = None) -> None:
    """Drop the ledger of one session, or every ledger when ``session`` is None."""
    with _lock:
        if session is None:
            _ledgers.clear()
        else:
            _ledgers.pop(session, None)
