"""Parser for git status porcelain output."""
from __future__ import annotations

from nerdvana_cli.ui.git_status import parse_porcelain


def test_parse_porcelain_modified_and_untracked() -> None:
    raw = " M src/foo.py\nA  tests/bar.py\n?? notes.md\n"
    rows = parse_porcelain(raw)
    assert rows == [
        ("M", "src/foo.py"),
        ("A", "tests/bar.py"),
        ("?", "notes.md"),
    ]


def test_parse_porcelain_deleted_and_renamed() -> None:
    raw = " D old.py\nR  from.py -> to.py\n"
    rows = parse_porcelain(raw)
    assert ("D", "old.py") in rows
    assert ("R", "from.py -> to.py") in rows


def test_parse_porcelain_empty_returns_empty() -> None:
    assert parse_porcelain("") == []
