"""Editing a symbol that was shown with anchors, without reading the whole file first.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from nerdvana_cli.codeintel.symbol import LanguageServerSymbol, Location, _sym_from_dict
from nerdvana_cli.core.tool import ToolContext
from nerdvana_cli.tools import read_ledger
from nerdvana_cli.tools.file_tools import FileEditArgs, FileEditTool
from nerdvana_cli.tools.symbol_tools import FindSymbolArgs, FindSymbolTool

SOURCE = (
    "import os\n"                       # 1
    "\n"                                # 2
    "def alpha(x):\n"                   # 3
    "    y = x + 1\n"                   # 4
    "    return y\n"                    # 5
    "\n"                                # 6
    "def beta(x):\n"                    # 7
    "    return x * 2\n"                # 8
    "\n"                                # 9
    "# unrelated footer\n"              # 10
)


def _symbol(name: str, start: int, end: int, file: str = "mod.py") -> LanguageServerSymbol:
    return LanguageServerSymbol(name=name, name_path=name, kind="Function", kind_int=12, location=Location(file, start, 0), end_line=end)


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    (tmp_path / "mod.py").write_text(SOURCE, encoding="utf-8")
    read_ledger.clear()
    yield tmp_path
    read_ledger.clear()


def _context(project: Path) -> ToolContext:
    context = ToolContext(cwd=str(project))
    context.state["session_id"] = "range-test"
    return context


async def _show(project: Path, symbols: list[LanguageServerSymbol], context: ToolContext) -> list[dict]:
    retriever = AsyncMock()
    retriever.find.return_value = symbols
    result = await FindSymbolTool(retriever=retriever).call(FindSymbolArgs("x", include_body=True), context)
    assert not result.is_error, result.content
    return json.loads(result.content)["matches"]  # type: ignore[no-any-return]


def _anchor(match: dict, line: int) -> str:
    for text in match["body"].splitlines():
        if text.startswith(f"{line}#"):
            return text.split()[0]
    raise AssertionError(f"line {line} not in the body")


async def _edit(project: Path, context: ToolContext, anchor: str, new: str) -> object:
    return await FileEditTool().call(FileEditArgs(path="mod.py", new_string=new, anchor_hash=anchor), context)


# ---------------------------------------------------------------------------
# Showing a symbol
# ---------------------------------------------------------------------------


async def test_a_symbol_is_shown_with_anchored_lines_and_its_range(project: Path) -> None:
    (match,) = await _show(project, [_symbol("alpha", 3, 5)], _context(project))
    assert match["lines"] == [3, 5] and "truncated" not in match
    assert [line.split()[0].split("#")[0] for line in match["body"].splitlines()] == ["3", "4", "5"]
    assert "    y = x + 1" in match["body"]
    assert read_ledger.lookup_ranges("range-test", read_ledger.resolve_key("mod.py", str(project)))[0][:2] == (3, 5)


async def test_a_symbol_without_an_extent_or_a_file_gets_no_body(project: Path) -> None:
    matches = await _show(project, [_symbol("alpha", 3, 0), _symbol("ghost", 1, 2, file="missing.py")], _context(project))
    assert all("body" not in m for m in matches)


async def test_long_bodies_are_cut_and_only_the_shown_part_is_recorded(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from nerdvana_cli.tools import symbol_tools

    monkeypatch.setattr(symbol_tools, "_MAX_BODY_LINES", 2)
    (match,) = await _show(project, [_symbol("alpha", 3, 5)], _context(project))
    assert match["lines"] == [3, 4] and match["truncated"] is True
    assert read_ledger.lookup_ranges("range-test", read_ledger.resolve_key("mod.py", str(project)))[0][:2] == (3, 4)


async def test_at_most_a_few_bodies_are_returned(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from nerdvana_cli.tools import symbol_tools

    monkeypatch.setattr(symbol_tools, "_MAX_BODIES", 1)
    matches = await _show(project, [_symbol("alpha", 3, 5), _symbol("beta", 7, 8)], _context(project))
    assert "body" in matches[0] and "body" not in matches[1]


def test_the_end_of_a_symbol_is_read_from_the_server_range() -> None:
    raw = {"name": "f", "kind": 12, "range": {"start": {"line": 2, "character": 0}, "end": {"line": 4, "character": 12}}}
    assert _sym_from_dict(raw, "a.py", "", 0, 1).end_line == 5
    assert _sym_from_dict({"name": "g", "kind": 12}, "a.py", "", 0, 1).end_line == 0


# ---------------------------------------------------------------------------
# Editing what was shown
# ---------------------------------------------------------------------------


async def test_a_shown_line_can_be_edited_without_reading_the_file(project: Path) -> None:
    context = _context(project)
    (match,) = await _show(project, [_symbol("alpha", 3, 5)], context)
    result  = await _edit(project, context, _anchor(match, 4), "    y = x + 100\n")
    assert not result.is_error, result.content  # type: ignore[attr-defined]
    assert "y = x + 100" in (project / "mod.py").read_text()


async def test_the_whole_file_is_still_unread_afterwards(project: Path) -> None:
    context = _context(project)
    (match,) = await _show(project, [_symbol("alpha", 3, 5)], context)
    await _edit(project, context, _anchor(match, 4), "    y = x + 100\n")
    other = await FileEditTool().call(FileEditArgs(path="mod.py", old_string="x * 2", new_string="x * 3"), context)
    assert other.is_error and "has not been read" in other.content


async def test_a_line_outside_the_shown_range_is_refused(project: Path) -> None:
    context = _context(project)
    (other,) = await _show(project, [_symbol("beta", 7, 8)], context)
    read_ledger.clear()                                       # beta's anchor is known, but only alpha's range is on record
    await _show(project, [_symbol("alpha", 3, 5)], context)
    result = await _edit(project, context, _anchor(other, 8), "    return x * 3\n")
    assert result.is_error and "has not been read" in result.content  # type: ignore[attr-defined]
    assert "x * 2" in (project / "mod.py").read_text()


async def test_a_change_inside_the_shown_range_makes_the_edit_stale(project: Path) -> None:
    context = _context(project)
    (match,) = await _show(project, [_symbol("alpha", 3, 5)], context)
    anchor = _anchor(match, 4)
    (project / "mod.py").write_text(SOURCE.replace("y = x + 1", "y = x + 2"), encoding="utf-8")
    result = await _edit(project, context, anchor, "    y = x + 100\n")
    assert result.is_error  # type: ignore[attr-defined]


async def test_a_change_elsewhere_in_the_file_does_not_stop_the_edit(project: Path) -> None:
    context = _context(project)
    (match,) = await _show(project, [_symbol("alpha", 3, 5)], context)
    anchor = _anchor(match, 4)
    (project / "mod.py").write_text(SOURCE.replace("# unrelated footer", "# edited by someone else"), encoding="utf-8")
    result = await _edit(project, context, anchor, "    y = x + 100\n")
    assert not result.is_error, result.content  # type: ignore[attr-defined]
    text = (project / "mod.py").read_text()
    assert "y = x + 100" in text and "edited by someone else" in text


async def test_the_range_grows_with_the_edit_so_the_symbol_can_be_edited_again(project: Path) -> None:
    context = _context(project)
    (match,) = await _show(project, [_symbol("alpha", 3, 5)], context)
    first   = await _edit(project, context, _anchor(match, 4), "    y = x + 1\n    z = y * 2\n")
    assert not first.is_error  # type: ignore[attr-defined]
    key = read_ledger.resolve_key("mod.py", str(project))
    assert [r[:2] for r in read_ledger.lookup_ranges("range-test", key)] == [(3, 6)]
    second = await _edit(project, context, _anchor(match, 5), "    return z\n")  # the anchor of the old line 5, now line 6
    assert not second.is_error, second.content  # type: ignore[attr-defined]
    assert "return z" in (project / "mod.py").read_text()


async def test_the_other_ranges_of_the_file_are_dropped_by_an_edit(project: Path) -> None:
    context = _context(project)
    matches = await _show(project, [_symbol("alpha", 3, 5), _symbol("beta", 7, 8)], context)
    await _edit(project, context, _anchor(matches[0], 4), "    y = x + 1\n    z = y\n")
    key = read_ledger.resolve_key("mod.py", str(project))
    assert [r[:2] for r in read_ledger.lookup_ranges("range-test", key)] == [(3, 6)]


async def test_a_whole_file_read_keeps_working_as_before(project: Path) -> None:
    from nerdvana_cli.tools.file_tools import FileReadArgs, FileReadTool

    context = _context(project)
    await FileReadTool().call(FileReadArgs(path="mod.py"), context)
    result = await FileEditTool().call(FileEditArgs(path="mod.py", old_string="x * 2", new_string="x * 3"), context)
    assert not result.is_error
