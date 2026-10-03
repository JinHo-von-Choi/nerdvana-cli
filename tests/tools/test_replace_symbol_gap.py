"""replace_symbol_body leaves the lines between the replaced symbol and the next statement as they were.

Without an extent from the language server the edit range runs up to the next line at the symbol's
indentation, so the blank lines and comment lines that follow the symbol's last statement lie inside it; with
the extent it is exactly the symbol. Each case replaces one symbol of a small file through the two-step tool
(preview, then apply) and compares the file text with what the file must contain, once with the extent a
language server reports (the 1-based last line of the symbol) and once without it.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import NamedTuple
from unittest.mock import AsyncMock

import pytest

from nerdvana_cli.core.code_editor import CodeEditor
from nerdvana_cli.core.symbol import LanguageServerSymbol, Location
from nerdvana_cli.core.symbol_lines import trim_trailing_gap
from nerdvana_cli.core.tool import ToolContext
from nerdvana_cli.tools import symbol_edit_tools as se


class Case(NamedTuple):
    """A file, the symbol to replace (name, 1-based first and last line), the body and the file afterwards."""

    source:   str
    name:     str
    first:    int
    last:     int
    body:     str
    expected: str


async def replace(root: Path, case: Case, with_extent: bool) -> str:
    """Replace the symbol of *case* in ``mod.py`` and return the new file text."""
    target = root / "mod.py"
    target.write_bytes(case.source.encode("utf-8"))
    symbol = LanguageServerSymbol(
        name      = case.name,
        name_path = case.name,
        kind      = "Function",
        kind_int  = 12,
        location  = Location(str(target), case.first, 0),
        end_line  = case.last if with_extent else 0,
    )
    retriever = AsyncMock()
    retriever.find     = AsyncMock(return_value=[symbol])
    retriever._resolve = lambda p: str(root / p)
    tool    = se.ReplaceSymbolBodyTool(retriever=retriever, editor=CodeEditor(project_root=str(root)))
    context = ToolContext(cwd=str(root))

    preview = await tool.call(se.ReplaceSymbolBodyArgs(name_path=case.name, relative_path="mod.py", body=case.body), context)
    assert not preview.is_error, preview.content
    applied = await tool.call(se.ReplaceSymbolBodyArgs(preview_id=json.loads(preview.content)["preview_id"], apply=True), context)
    assert json.loads(applied.content)["status"] == "applied", applied.content
    return target.read_bytes().decode("utf-8")


BOTH_WAYS = {
    "blank lines after a function": Case(
        "def first():\n    return 1\n\n\ndef second():\n    return 2\n", "first", 1, 2,
        "def first():\n    return 10\n",
        "def first():\n    return 10\n\n\ndef second():\n    return 2\n",
    ),
    "nested method before another method": Case(
        "class Box:\n    def open(self):\n        return 1\n\n    def close(self):\n        return 2\n\n\ndef tail():\n    pass\n", "open", 2, 3,
        "    def open(self):\n        return 5\n",
        "class Box:\n    def open(self):\n        return 5\n\n    def close(self):\n        return 2\n\n\ndef tail():\n    pass\n",
    ),
    "last method of a class": Case(
        "class Box:\n    def open(self):\n        return 1\n\n    def close(self):\n        return 2\n\n\ndef tail():\n    pass\n", "close", 5, 6,
        "    def close(self):\n        return 7\n",
        "class Box:\n    def open(self):\n        return 1\n\n    def close(self):\n        return 7\n\n\ndef tail():\n    pass\n",
    ),
    "comment after the last statement": Case(
        "def first():\n    return 1\n    # kept note\n\n# about second\ndef second():\n    return 2\n", "first", 1, 2,
        "def first():\n    return 4\n",
        "def first():\n    return 4\n    # kept note\n\n# about second\ndef second():\n    return 2\n",
    ),
    "comment inside the body is replaced with it": Case(
        "def first():\n    a = 1\n    # inside\n    return a  # on the last line\n\n\ndef second():\n    pass\n", "first", 1, 4,
        "def first():\n    return 0\n",
        "def first():\n    return 0\n\n\ndef second():\n    pass\n",
    ),
    "blank lines at the end of the file": Case(
        "def first():\n    return 1\n\n\ndef last():\n    return 2\n\n\n", "last", 5, 6,
        "def last():\n    return 3\n",
        "def first():\n    return 1\n\n\ndef last():\n    return 3\n\n\n",
    ),
    "last symbol without a final newline": Case(
        "def first():\n    return 1\n\n\ndef last():\n    return 2", "last", 5, 6,
        "def last():\n    return 3",
        "def first():\n    return 1\n\n\ndef last():\n    return 3",
    ),
    "body without a final newline before a gap": Case(
        "def first():\n    return 1\n\n\ndef second():\n    pass\n", "first", 1, 2,
        "def first():\n    return 9",
        "def first():\n    return 9\n\n\ndef second():\n    pass\n",
    ),
}


@pytest.mark.parametrize("with_extent", [True, False], ids=["with extent", "without extent"])
@pytest.mark.parametrize("case", BOTH_WAYS.values(), ids=BOTH_WAYS.keys())
async def test_the_text_around_the_symbol_is_kept(tmp_path: Path, case: Case, with_extent: bool) -> None:
    assert await replace(tmp_path, case, with_extent) == case.expected


async def test_a_decorated_function_is_replaced_from_its_first_decorator_and_keeps_the_gap(tmp_path: Path) -> None:
    case = Case(
        "import functools\n\n\n@functools.cache\n@staticmethod\ndef first():\n    return 1\n\n\n\ndef second():\n    return 2\n", "first", 4, 7,
        "@functools.lru_cache\ndef first():\n    return 3\n",
        "import functools\n\n\n@functools.lru_cache\ndef first():\n    return 3\n\n\n\ndef second():\n    return 2\n",
    )

    assert await replace(tmp_path, case, with_extent=True) == case.expected


async def test_a_signature_over_several_lines_does_not_end_the_symbol_early(tmp_path: Path) -> None:
    case = Case(
        "def first(\n    a,\n    b,\n) -> int:\n    return a + b\n\n\ndef second():\n    pass\n", "first", 1, 5,
        "def first(a, b) -> int:\n    return a * b\n",
        "def first(a, b) -> int:\n    return a * b\n\n\ndef second():\n    pass\n",
    )

    assert await replace(tmp_path, case, with_extent=True) == case.expected


class TestTrimTrailingGap:
    def test_blank_and_comment_lines_at_the_end_of_the_range_are_left_out(self) -> None:
        lines = ["def f():\n", "    x = 1\n", "    # note\n", "\n", "\n", "def g():\n"]
        assert trim_trailing_gap(lines, 0, 5) == 2

    def test_slash_comments_count_for_other_languages(self) -> None:
        lines = ["function f() {\n", "  x();\n", "  // note\n", "\n"]
        assert trim_trailing_gap(lines, 0, 4) == 2

    def test_the_range_never_shrinks_below_the_symbol_header(self) -> None:
        assert trim_trailing_gap(["# only a comment\n", "\n"], 0, 2) == 1

    @pytest.mark.parametrize("end", [1, 3])
    def test_a_range_that_ends_on_a_statement_is_unchanged(self, end: int) -> None:
        assert trim_trailing_gap(["def f():\n", "    a = 1\n", "    return a\n"], 0, end) == end
