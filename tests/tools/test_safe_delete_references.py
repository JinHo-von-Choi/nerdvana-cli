"""safe_delete_symbol is blocked by references from outside the symbol, not by the symbol's own definition.

``textDocument/references`` includes the declaration, so the definition line (and a recursive call) come back
as references of every symbol; only uses elsewhere may block the deletion.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock

from nerdvana_cli.core.code_editor import CodeEditor
from nerdvana_cli.core.symbol import LanguageServerSymbol, Location
from nerdvana_cli.core.symbol_lines import outside_symbol, with_trailing_blank_lines
from nerdvana_cli.core.tool import ToolContext
from nerdvana_cli.tools import symbol_edit_tools as se

SOURCE = "def keep():\n    return 1\n\n\ndef dead(n):\n    if n:\n        return dead(n - 1)\n    return 0\n\n\ndef tail():\n    pass\n"


async def preview(root: Path, refs: list[Location], apply: bool = False) -> dict[str, object]:
    """Step 1 of deleting ``dead`` (lines 5 to 8 of ``src.py``) when the server reports *refs*; with *apply* step 2 too."""
    target = root / "src.py"
    target.write_text(SOURCE, encoding="utf-8")
    symbol = LanguageServerSymbol(
        name="dead", name_path="dead", kind="Function", kind_int=12, location=Location(str(target), 5, 0), end_line=8,
    )
    retriever = AsyncMock()
    retriever.find            = AsyncMock(return_value=[symbol])
    retriever.find_references = AsyncMock(return_value=refs)
    retriever._resolve        = lambda p: str(root / p)
    tool   = se.SafeDeleteSymbolTool(retriever=retriever, editor=CodeEditor(project_root=str(root)))
    result = await tool.call(se.SafeDeleteSymbolArgs(name_path="dead", relative_path="src.py"), ToolContext(cwd=str(root)))
    assert not result.is_error, result.content
    payload = dict(json.loads(result.content))
    if apply:
        applied = await tool.call(se.SafeDeleteSymbolArgs(preview_id=str(payload["preview_id"]), apply=True), ToolContext(cwd=str(root)))
        assert json.loads(applied.content)["status"] == "applied", applied.content
    return payload


async def test_a_symbol_whose_only_references_are_its_own_definition_and_recursion_can_be_deleted(tmp_path: Path) -> None:
    src  = str(tmp_path / "src.py")
    refs = [Location(src, 5, 4), Location(src, 7, 15)]

    payload = await preview(tmp_path, refs)

    assert payload["kind"] == "delete"
    assert "-def dead(n):" in str(payload["diff"])


async def test_deleting_removes_the_symbol_and_the_blank_lines_after_it_only(tmp_path: Path) -> None:
    await preview(tmp_path, [Location(str(tmp_path / "src.py"), 5, 4)], apply=True)

    assert (tmp_path / "src.py").read_text(encoding="utf-8") == "def keep():\n    return 1\n\n\ndef tail():\n    pass\n"


async def test_a_use_in_another_file_blocks_the_deletion_and_is_the_only_one_listed(tmp_path: Path) -> None:
    src  = str(tmp_path / "src.py")
    refs = [Location(src, 5, 4), Location(str(tmp_path / "other.py"), 6, 0)]

    payload = await preview(tmp_path, refs)

    assert payload["status"] == "blocked_by_references"
    assert payload["references"] == [{"file": str(tmp_path / "other.py"), "line": 6, "character": 0}]


async def test_a_use_in_the_same_file_outside_the_symbol_blocks_the_deletion(tmp_path: Path) -> None:
    src  = str(tmp_path / "src.py")
    refs = [Location(src, 5, 4), Location(src, 12, 4)]

    payload = await preview(tmp_path, refs)

    assert payload["status"] == "blocked_by_references"
    assert payload["references"] == [{"file": src, "line": 12, "character": 4}]


class TestWithTrailingBlankLines:
    def test_blank_lines_after_the_range_are_taken_in(self) -> None:
        assert with_trailing_blank_lines(["a\n", "\n", "  \n", "b\n"], 1) == 3

    def test_the_end_of_the_file_is_the_limit(self) -> None:
        assert with_trailing_blank_lines(["a\n", "\n"], 1) == 2
        assert with_trailing_blank_lines(["a\n"], 1) == 1


class TestOutsideSymbol:
    def test_the_range_is_one_based_on_the_reference_and_zero_based_exclusive_on_the_symbol(self) -> None:
        refs = [Location("/p/a.py", line, 0) for line in (4, 5, 8, 9)]
        assert [r.line for r in outside_symbol(refs, "/p/a.py", 4, 8)] == [4, 9]

    def test_the_same_line_numbers_in_another_file_stay(self) -> None:
        refs = [Location("/p/b.py", 6, 0)]
        assert outside_symbol(refs, "/p/a.py", 4, 8) == refs
