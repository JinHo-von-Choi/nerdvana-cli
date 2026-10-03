"""References across files with a real pyright, on a tiny multi-file project.

Pyright answers ``textDocument/references`` only from the documents it has been shown, so a client that
opens the one file it was asked about returns the definition and nothing else. Run with
``pytest tests/lsp/test_references_integration.py -m lsp_integration``; needs ``pyright-langserver`` on PATH.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

import json
import shutil
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from nerdvana_cli.core.code_editor import CodeEditor
from nerdvana_cli.core.lsp_client import LspClient
from nerdvana_cli.core.symbol import LanguageServerSymbolRetriever
from nerdvana_cli.core.tool import ToolContext
from nerdvana_cli.tools.symbol_edit_tools import SafeDeleteSymbolArgs, SafeDeleteSymbolTool

pytestmark = [
    pytest.mark.lsp_integration,
    pytest.mark.skipif(shutil.which("pyright-langserver") is None, reason="pyright-langserver not installed"),
]


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """``pkg.core.compute`` is imported by ``pkg/use_import.py`` and called through its module by ``use_module.py``."""
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "pkg" / "core.py").write_text("def compute(x: int) -> int:\n    return x + 1\n", encoding="utf-8")
    (tmp_path / "pkg" / "use_import.py").write_text(
        "from pkg.core import compute\n\n\ndef run() -> int:\n    return compute(1)\n", encoding="utf-8"
    )
    (tmp_path / "use_module.py").write_text("import pkg.core\n\nVALUE = pkg.core.compute(2)\n", encoding="utf-8")
    (tmp_path / "unrelated.py").write_text("def compute_other() -> int:\n    return 3\n", encoding="utf-8")
    return tmp_path


@pytest.fixture
async def retriever(project: Path) -> AsyncIterator[LanguageServerSymbolRetriever]:
    client = LspClient(project_root=str(project))
    yield LanguageServerSymbolRetriever(client=client, project_root=str(project))
    await client.close()


async def test_references_reach_every_file_that_uses_the_symbol(retriever: LanguageServerSymbolRetriever) -> None:
    symbols = await retriever.find("compute", within="pkg/core.py")

    refs = await retriever.find_references(symbols[0])

    assert {Path(r.file_path).name for r in refs} == {"core.py", "use_import.py", "use_module.py"}
    assert "unrelated.py" not in {Path(r.file_path).name for r in refs}


async def test_a_reference_removed_from_a_file_is_not_reported_afterwards(
    retriever: LanguageServerSymbolRetriever, project: Path
) -> None:
    symbols = await retriever.find("compute", within="pkg/core.py")
    assert "use_module.py" in {Path(r.file_path).name for r in await retriever.find_references(symbols[0])}

    (project / "use_module.py").write_text("VALUE = 2\n", encoding="utf-8")
    refs = await retriever.find_references(symbols[0])

    assert {Path(r.file_path).name for r in refs} == {"core.py", "use_import.py"}


async def test_safe_delete_is_blocked_by_uses_in_other_files_but_not_by_the_definition(
    retriever: LanguageServerSymbolRetriever, project: Path
) -> None:
    tool    = SafeDeleteSymbolTool(retriever=retriever, editor=CodeEditor(project_root=str(project)))
    context = ToolContext(cwd=str(project))

    blocked = json.loads((await tool.call(SafeDeleteSymbolArgs(name_path="compute", relative_path="pkg/core.py"), context)).content)
    free    = json.loads((await tool.call(SafeDeleteSymbolArgs(name_path="compute_other", relative_path="unrelated.py"), context)).content)

    assert blocked["status"] == "blocked_by_references"
    assert {Path(r["file"]).name for r in blocked["references"]} == {"use_import.py", "use_module.py"}
    assert free["kind"] == "delete"
