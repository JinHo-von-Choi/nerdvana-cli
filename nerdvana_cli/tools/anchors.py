"""Line anchors of FileRead and FileEdit: ``N#hhhhhh``, a line number and the first six hex digits of the line's sha256."""

from __future__ import annotations

import hashlib
import re

_HASH_LEN:         int = 6
_RELOCATE_WINDOW:  int = 20

_ANCHOR_RE = re.compile(rf"^(\d+)#([0-9a-f]{{{_HASH_LEN}}})$")


def _line_hash(line: str) -> str:
    """Return the first 6 hex chars of sha256 over the line without its newline."""
    return hashlib.sha256(line.rstrip("\n").encode()).hexdigest()[:_HASH_LEN]


def _parse_anchor(anchor: str) -> tuple[int, str] | None:
    """Split an ``N#hhhhhh`` anchor into (line number, hash); None when malformed."""
    match = _ANCHOR_RE.match(anchor.strip())
    if match is None:
        return None
    return int(match.group(1)), match.group(2)


def _resolve_anchor(lineno: int, line_hash: str, lines: list[str]) -> int | None:
    """Return the 0-based index of the line an anchor names, or None.

    The hash at ``lineno`` wins outright.  Otherwise the lines within
    ``_RELOCATE_WINDOW`` of it are searched for the hash, and a relocation is
    accepted only when exactly one of them carries it.  No similarity matching
    is attempted.
    """
    idx = lineno - 1
    if 0 <= idx < len(lines) and _line_hash(lines[idx]) == line_hash:
        return idx
    low        = max(0, idx - _RELOCATE_WINDOW)
    high       = min(len(lines) - 1, idx + _RELOCATE_WINDOW)
    candidates = [i for i in range(low, high + 1) if _line_hash(lines[i]) == line_hash]
    return candidates[0] if len(candidates) == 1 else None


def _with_line_ending(replacement: str, old_line: str) -> str:
    """*replacement* ending the way *old_line* did, so the line after it is not joined onto the new text."""
    ending = old_line[len(old_line.rstrip("\r\n")):]
    return replacement + ending if ending and not replacement.endswith(("\n", "\r")) else replacement
