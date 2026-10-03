"""Line arithmetic of the symbol edit tools: what lies inside a symbol's range and what does not.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

import os
from typing import Any

_COMMENT_PREFIXES = ("#", "//")


def trim_trailing_gap(lines: list[str], start_line: int, end_line: int) -> int:
    """Return *end_line* moved up past the blank and comment-only lines that end the range.

    A range that runs up to the next line at the symbol's indentation holds the blank lines (and any comment
    lines) between the symbol's last statement and the next statement. A replacement must leave them where
    they are. *start_line* and *end_line* are 0-based, the end exclusive; the range keeps at least one line.
    """
    while end_line > start_line + 1:
        text = lines[end_line - 1].strip()
        if text and not text.startswith(_COMMENT_PREFIXES):
            break
        end_line -= 1
    return end_line


def with_trailing_blank_lines(lines: list[str], end_line: int) -> int:
    """Return *end_line* (0-based, exclusive) moved down past the blank lines that follow it."""
    while end_line < len(lines) and not lines[end_line].strip():
        end_line += 1
    return end_line


def outside_symbol(refs: list[Any], abs_path: str, start_line: int, end_line: int) -> list[Any]:
    """The references that are not inside the symbol itself (its definition line, a recursive call).

    *start_line* and *end_line* are 0-based, the end exclusive; a reference line is 1-based.
    """
    target = os.path.realpath(abs_path)
    return [r for r in refs if os.path.realpath(r.file_path) != target or not start_line < r.line <= end_line]
