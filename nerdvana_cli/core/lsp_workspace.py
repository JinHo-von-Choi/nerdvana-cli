"""Which workspace files a language server has to see before it can answer a cross-file question.

Author: 최진호
Date:   2026-10-03

A language server answers ``textDocument/references`` and ``textDocument/rename`` only from the
documents it has been given. Pyright, for one, treats the files of the workspace as indexed lazily and
reports the definition alone until the other files are opened, so the client opens every file that
mentions the identifier before it asks.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterable
from typing import Any, NamedTuple

MAX_REFERENCE_FILES: int            = 200
MAX_SCANNED_BYTES:   int            = 1_000_000
SKIPPED_DIRS:        frozenset[str] = frozenset({
    "node_modules", "__pycache__", "site-packages", "venv", "build", "dist", "target",
})


class Mentions(NamedTuple):
    """Files that mention an identifier: the ones to open, and how many more there were."""

    files:   list[str]
    omitted: int


class NoticedList(list[Any]):
    """A result list that says what may be missing from it; ``notice`` is empty when nothing is."""

    notice: str

    def __init__(self, items: Iterable[Any] = (), notice: str = "") -> None:
        super().__init__(items)
        self.notice = notice


def notice_of(result: object) -> str:
    """The notice a result list carries, or an empty string for a plain list."""
    return getattr(result, "notice", "")


def _is_skipped(directory: str) -> bool:
    return directory.startswith(".") or directory in SKIPPED_DIRS


def _closeness(origin_dir: str, path: str) -> tuple[int, str]:
    """Sort key: files sharing more of the origin's directory path come first, then by name."""
    return (-len(os.path.commonpath([origin_dir, os.path.dirname(path)])), path)


def mentioning_files(root: str, suffix: str, identifier: str, origin: str, limit: int = MAX_REFERENCE_FILES) -> Mentions:
    """Files under *root* ending in *suffix* whose text contains *identifier* as a whole word.

    Hidden directories, dependency and build directories and files above ``MAX_SCANNED_BYTES`` are not
    read. At most *limit* files come back, those closest to the directory of *origin* first; ``omitted``
    counts the matches left out. Blocking: run it in a thread.
    """
    pattern    = re.compile(rf"(?<!\w){re.escape(identifier)}(?!\w)")
    origin_dir = os.path.dirname(os.path.abspath(origin))
    found: list[str] = []
    for directory, subdirectories, names in os.walk(os.path.abspath(root)):
        subdirectories[:] = sorted(d for d in subdirectories if not _is_skipped(d))
        for name in sorted(names):
            path = os.path.join(directory, name)
            if name.endswith(suffix) and _mentions(path, pattern):
                found.append(path)
    ranked = sorted(found, key=lambda path: _closeness(origin_dir, path))
    return Mentions(ranked[:limit], max(len(ranked) - limit, 0))


def _mentions(path: str, pattern: re.Pattern[str]) -> bool:
    try:
        if os.path.getsize(path) > MAX_SCANNED_BYTES:
            return False
        with open(path, encoding="utf-8") as handle:
            return pattern.search(handle.read()) is not None
    except (OSError, UnicodeDecodeError):
        return False
