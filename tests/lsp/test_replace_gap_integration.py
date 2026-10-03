"""replace_symbol_body on a real pyright: the extent the server reports, the lines around the symbol stay.

Run with ``pytest tests/lsp/test_replace_gap_integration.py -m lsp_integration``; needs ``pyright-langserver`` on PATH.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

import json
import shutil
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from nerdvana_cli.codeintel.code_editor import CodeEditor
from nerdvana_cli.codeintel.lsp_client import LspClient
from nerdvana_cli.codeintel.symbol import LanguageServerSymbolRetriever
from nerdvana_cli.core.tool import ToolContext
from nerdvana_cli.tools.symbol_edit_tools import ReplaceSymbolBodyArgs, ReplaceSymbolBodyTool

pytestmark = [
    pytest.mark.lsp_integration,
    pytest.mark.skipif(shutil.which("pyright-langserver") is None, reason="pyright-langserver not installed"),
]

SOURCE = '''\
import functools


@functools.cache
def decorated(a: int) -> int:
    return a  # trailing note
    # comment after the last statement



def wrapped(
    first: int,
    second: int,
) -> int:
    return first + second


class Box:
    def open(self) -> int:
        return 1

    def close(self) -> int:
        return 2


def last() -> int:
    return 3

'''


@pytest.fixture
async def tool(tmp_path: Path) -> AsyncIterator[ReplaceSymbolBodyTool]:
    (tmp_path / "mod.py").write_text(SOURCE, encoding="utf-8")
    client = LspClient(project_root=str(tmp_path))
    yield ReplaceSymbolBodyTool(
        retriever=LanguageServerSymbolRetriever(client=client, project_root=str(tmp_path)),
        editor=CodeEditor(project_root=str(tmp_path)),
    )
    await client.close()


async def replace(tool: ReplaceSymbolBodyTool, root: Path, name_path: str, body: str) -> str:
    context = ToolContext(cwd=str(root))
    preview = await tool.call(ReplaceSymbolBodyArgs(name_path=name_path, relative_path="mod.py", body=body), context)
    assert not preview.is_error, preview.content
    applied = await tool.call(ReplaceSymbolBodyArgs(preview_id=json.loads(preview.content)["preview_id"], apply=True), context)
    assert json.loads(applied.content)["status"] == "applied", applied.content
    return (root / "mod.py").read_text(encoding="utf-8")


async def test_a_decorated_function_is_replaced_and_the_comment_and_blank_lines_after_it_stay(
    tool: ReplaceSymbolBodyTool, tmp_path: Path
) -> None:
    result = await replace(tool, tmp_path, "decorated", "@functools.cache\ndef decorated(a: int) -> int:\n    return a + 1\n")

    assert result == SOURCE.replace("    return a  # trailing note\n", "    return a + 1\n")


async def test_a_signature_over_several_lines_is_replaced_whole(tool: ReplaceSymbolBodyTool, tmp_path: Path) -> None:
    result = await replace(tool, tmp_path, "wrapped", "def wrapped(first: int, second: int) -> int:\n    return first * second\n")

    assert result == SOURCE.replace(
        "def wrapped(\n    first: int,\n    second: int,\n) -> int:\n    return first + second\n",
        "def wrapped(first: int, second: int) -> int:\n    return first * second\n",
    )


async def test_a_method_and_the_last_function_keep_their_blank_lines(tool: ReplaceSymbolBodyTool, tmp_path: Path) -> None:
    result = await replace(tool, tmp_path, "Box/open", "    def open(self) -> int:\n        return 10\n")
    result = await replace(tool, tmp_path, "last", "def last() -> int:\n    return 30\n")

    assert result == SOURCE.replace("        return 1\n", "        return 10\n").replace("    return 3\n", "    return 30\n")
