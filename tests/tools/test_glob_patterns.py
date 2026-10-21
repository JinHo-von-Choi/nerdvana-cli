"""Glob: ``**/`` covers zero or more directories, so top-level files and direct children match."""

from __future__ import annotations

from pathlib import Path

import pytest

from nerdvana_cli.core.tool import ToolContext
from nerdvana_cli.tools.search_tools import GlobArgs, GlobTool, glob_regex


@pytest.mark.parametrize(
    ("pattern", "path", "matches"),
    [
        ("**/*", "calc.py", True),
        ("**/*", "a/b/c.py", True),
        ("src/**/*.tsx", "src/a.tsx", True),
        ("src/**/*.tsx", "src/x/y/a.tsx", True),
        ("src/**/*.tsx", "lib/a.tsx", False),
        ("**/test_*.py", "test_top.py", True),
        ("*.py", "a/b.py", False),
        ("[!a]*.py", "b.py", True),
        ("[!a]*.py", "a.py", False),
        ("?.py", "ab.py", False),
    ],
)
def test_glob_regex(pattern: str, path: str, matches: bool) -> None:
    assert bool(glob_regex(pattern).match(path)) is matches


async def test_a_top_level_file_is_found_by_a_recursive_pattern(tmp_path: Path) -> None:
    (tmp_path / "calc.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "mod.py").write_text("y = 2\n", encoding="utf-8")
    result = await GlobTool().call(GlobArgs(pattern="**/*.py"), ToolContext(cwd=str(tmp_path)))
    assert "calc.py" in result.content and "pkg/mod.py" in result.content


async def test_a_pattern_without_a_slash_still_matches_names_anywhere(tmp_path: Path) -> None:
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "mod.py").write_text("y = 2\n", encoding="utf-8")
    result = await GlobTool().call(GlobArgs(pattern="*.py"), ToolContext(cwd=str(tmp_path)))
    assert "pkg/mod.py" in result.content
