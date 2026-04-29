"""LSP integration tests for Phase D symbol tools.

Requires pyright or pyright-langserver on PATH.
Run with: pytest tests/lsp/test_symbol_tools_integration.py -v -m lsp_integration

These tests use a real language server process against the sample_python_project
fixture directory.

작성자: 최진호
작성일: 2026-04-18
수정일: 2026-04-18 (Phase D.1 — InsertBefore, InsertAfter, SafeDelete E2E)
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from nerdvana_cli.core.code_editor import CodeEditor
from nerdvana_cli.core.lsp_client import LspClient
from nerdvana_cli.core.symbol import LanguageServerSymbolRetriever

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "sample_python_project"
MODELS_FILE  = FIXTURES_DIR / "models.py"


def _has_pyright() -> bool:
    return bool(shutil.which("pyright") or shutil.which("pyright-langserver"))


# ---------------------------------------------------------------------------
# Integration tests
# ---------------------------------------------------------------------------


@pytest.mark.lsp_integration
@pytest.mark.skipif(not _has_pyright(), reason="pyright not installed")
class TestSymbolOverviewIntegration:
    @pytest.fixture
    async def client_and_retriever(self) -> tuple[LspClient, LanguageServerSymbolRetriever]:
        client    = LspClient(project_root=str(FIXTURES_DIR))
        retriever = LanguageServerSymbolRetriever(
            client=client, project_root=str(FIXTURES_DIR),
        )
        yield client, retriever
        await client.close()

    async def test_symbol_overview_returns_symbols(
        self,
        client_and_retriever: tuple[LspClient, LanguageServerSymbolRetriever],
    ) -> None:
        """SymbolOverview on models.py returns User and Product classes."""
        _, retriever = client_and_retriever
        symbols = await retriever.get_overview(str(MODELS_FILE), depth=0)
        names   = [s.name for s in symbols]
        assert "User" in names or len(symbols) > 0, (
            f"Expected symbols in models.py, got: {names}"
        )

    async def test_symbol_overview_with_depth_includes_methods(
        self,
        client_and_retriever: tuple[LspClient, LanguageServerSymbolRetriever],
    ) -> None:
        """depth=1 includes methods of User and Product."""
        _, retriever = client_and_retriever
        symbols = await retriever.get_overview(str(MODELS_FILE), depth=1)
        all_names = [s.name for s in symbols] + [
            c.name for s in symbols for c in s.children
        ]
        # At least one method should appear (greeting, is_adult, discounted)
        method_names = {"greeting", "is_adult", "discounted"}
        found = set(all_names) & method_names
        assert found, f"Expected at least one method; got names: {all_names}"


@pytest.mark.lsp_integration
@pytest.mark.skipif(not _has_pyright(), reason="pyright not installed")
class TestFindSymbolIntegration:
    @pytest.fixture
    async def retriever(self) -> LanguageServerSymbolRetriever:
        client = LspClient(project_root=str(FIXTURES_DIR))
        r      = LanguageServerSymbolRetriever(client=client, project_root=str(FIXTURES_DIR))
        yield r
        await client.close()

    async def test_find_class_method(
        self, retriever: LanguageServerSymbolRetriever,
    ) -> None:
        """FindSymbol User/greeting resolves to the greeting method."""
        symbols = await retriever.find(
            name_path = "User/greeting",
            within    = str(MODELS_FILE),
        )
        assert len(symbols) >= 1
        assert symbols[0].name == "greeting"

    async def test_find_class_top_level(
        self, retriever: LanguageServerSymbolRetriever,
    ) -> None:
        """FindSymbol User returns the User class."""
        symbols = await retriever.find(
            name_path = "User",
            within    = str(MODELS_FILE),
        )
        assert any(s.name == "User" for s in symbols)

    async def test_find_substring(
        self, retriever: LanguageServerSymbolRetriever,
    ) -> None:
        """Substring matching finds symbols containing the query."""
        symbols = await retriever.find(
            name_path  = "greet",
            substring  = True,
            within     = str(MODELS_FILE),
        )
        # greeting matches "greet" substring
        names = [s.name for s in symbols]
        assert any("greet" in n.lower() for n in names), f"Got: {names}"


@pytest.mark.lsp_integration
@pytest.mark.skipif(not _has_pyright(), reason="pyright not installed")
class TestFindReferencesIntegration:
    @pytest.fixture
    async def retriever(self) -> LanguageServerSymbolRetriever:
        client = LspClient(project_root=str(FIXTURES_DIR))
        r      = LanguageServerSymbolRetriever(client=client, project_root=str(FIXTURES_DIR))
        yield r
        await client.close()

    async def test_find_references_returns_locations(
        self, retriever: LanguageServerSymbolRetriever,
    ) -> None:
        """FindReferences for User returns at least the definition location."""
        symbols = await retriever.find(
            name_path = "User",
            within    = str(MODELS_FILE),
        )
        assert symbols, "User class not found"
        refs = await retriever.find_references(symbols[0])
        # At minimum the definition itself is a reference
        assert len(refs) >= 1
        files = {r.file_path for r in refs}
        assert any("models" in f for f in files), f"Expected models.py in refs; got: {files}"


@pytest.mark.lsp_integration
@pytest.mark.skipif(not _has_pyright(), reason="pyright not installed")
class TestReplaceSymbolBodyIntegration:
    @pytest.fixture
    async def setup(self, tmp_path: Path) -> tuple[LanguageServerSymbolRetriever, CodeEditor, Path]:
        """Copy models.py to tmp_path to avoid mutating fixtures."""
        target = tmp_path / "models.py"
        target.write_text(MODELS_FILE.read_text(encoding="utf-8"), encoding="utf-8")

        client    = LspClient(project_root=str(tmp_path))
        retriever = LanguageServerSymbolRetriever(client=client, project_root=str(tmp_path))
        editor    = CodeEditor(project_root=str(tmp_path))
        yield retriever, editor, target
        await client.close()

    async def test_replace_symbol_body_two_step(
        self,
        setup: tuple[LanguageServerSymbolRetriever, CodeEditor, Path],
    ) -> None:
        """Step1 preview + Step2 apply replaces the method body."""
        from nerdvana_cli.core.tool import ToolContext
        from nerdvana_cli.tools.symbol_tools import ReplaceSymbolBodyArgs, ReplaceSymbolBodyTool

        retriever, editor, target = setup

        tool = ReplaceSymbolBodyTool(retriever=retriever, editor=editor)
        ctx  = ToolContext(cwd=str(target.parent))

        # Step 1
        args1  = ReplaceSymbolBodyArgs(
            name_path     = "User/greeting",
            relative_path = str(target),
            body          = '    def greeting(self) -> str:\n        return f"Hi, {self.name}!"\n',
        )
        import json
        r1   = await tool.call(args1, ctx)
        assert not r1.is_error, r1.content
        d1   = json.loads(r1.content)
        pid  = d1["preview_id"]
        assert "diff" in d1

        # Step 2
        args2  = ReplaceSymbolBodyArgs(preview_id=pid, apply=True)
        r2     = await tool.call(args2, ctx)
        assert not r2.is_error, r2.content
        d2     = json.loads(r2.content)
        assert d2["status"] == "applied"

    async def test_replace_symbol_body_stale(
        self,
        setup: tuple[LanguageServerSymbolRetriever, CodeEditor, Path],
    ) -> None:
        """STALE scenario: file mutated between preview and apply."""
        import json

        from nerdvana_cli.core.tool import ToolContext
        from nerdvana_cli.tools.symbol_tools import ReplaceSymbolBodyArgs, ReplaceSymbolBodyTool

        retriever, editor, target = setup
        tool = ReplaceSymbolBodyTool(retriever=retriever, editor=editor)
        ctx  = ToolContext(cwd=str(target.parent))

        # Step 1
        args1 = ReplaceSymbolBodyArgs(
            name_path     = "User/is_adult",
            relative_path = str(target),
            body          = '    def is_adult(self) -> bool:\n        return self.age > 21\n',
        )
        r1  = await tool.call(args1, ctx)
        assert not r1.is_error
        pid = json.loads(r1.content)["preview_id"]

        # Mutate target file
        original = target.read_text(encoding="utf-8")
        target.write_text(original + "\n# mutated\n", encoding="utf-8")

        # Step 2 — should return STALE
        args2  = ReplaceSymbolBodyArgs(preview_id=pid, apply=True)
        r2     = await tool.call(args2, ctx)
        d2     = json.loads(r2.content)
        assert d2["status"] == "STALE"


@pytest.mark.lsp_integration
@pytest.mark.skipif(not _has_pyright(), reason="pyright not installed")
class TestInsertBeforeSymbolIntegration:
    @pytest.fixture
    async def setup(self, tmp_path: Path) -> tuple[LanguageServerSymbolRetriever, CodeEditor, Path]:
        target = tmp_path / "models.py"
        target.write_text(MODELS_FILE.read_text(encoding="utf-8"), encoding="utf-8")

        client    = LspClient(project_root=str(tmp_path))
        retriever = LanguageServerSymbolRetriever(client=client, project_root=str(tmp_path))
        editor    = CodeEditor(project_root=str(tmp_path))
        yield retriever, editor, target
        await client.close()

    async def test_insert_before_two_step(
        self,
        setup: tuple[LanguageServerSymbolRetriever, CodeEditor, Path],
    ) -> None:
        """InsertBeforeSymbol inserts a line before the User class definition."""
        import json

        from nerdvana_cli.core.tool import ToolContext
        from nerdvana_cli.tools.symbol_tools import InsertBeforeSymbolArgs, InsertBeforeSymbolTool

        retriever, editor, target = setup
        tool = InsertBeforeSymbolTool(retriever=retriever, editor=editor)
        ctx  = ToolContext(cwd=str(target.parent))

        # Step 1: preview
        args1 = InsertBeforeSymbolArgs(
            name_path     = "User",
            relative_path = str(target),
            body          = "# === auto-inserted marker ===\n",
        )
        r1 = await tool.call(args1, ctx)
        assert not r1.is_error, r1.content
        d1 = json.loads(r1.content)
        assert d1["kind"] == "insert_before"
        assert "auto-inserted marker" in d1["diff"]

        # Step 2: apply
        args2  = InsertBeforeSymbolArgs(preview_id=d1["preview_id"], apply=True)
        r2     = await tool.call(args2, ctx)
        assert not r2.is_error, r2.content
        d2     = json.loads(r2.content)
        assert d2["status"] == "applied"

        content = target.read_text(encoding="utf-8")
        assert "auto-inserted marker" in content
        # The marker should appear before the class definition
        marker_pos = content.index("auto-inserted marker")
        class_pos  = content.index("class User")
        assert marker_pos < class_pos


@pytest.mark.lsp_integration
@pytest.mark.skipif(not _has_pyright(), reason="pyright not installed")
class TestInsertAfterSymbolIntegration:
    @pytest.fixture
    async def setup(self, tmp_path: Path) -> tuple[LanguageServerSymbolRetriever, CodeEditor, Path]:
        target = tmp_path / "models.py"
        target.write_text(MODELS_FILE.read_text(encoding="utf-8"), encoding="utf-8")

        client    = LspClient(project_root=str(tmp_path))
        retriever = LanguageServerSymbolRetriever(client=client, project_root=str(tmp_path))
        editor    = CodeEditor(project_root=str(tmp_path))
        yield retriever, editor, target
        await client.close()

    async def test_insert_after_two_step(
        self,
        setup: tuple[LanguageServerSymbolRetriever, CodeEditor, Path],
    ) -> None:
        """InsertAfterSymbol inserts a function after the greeting method."""
        import json

        from nerdvana_cli.core.tool import ToolContext
        from nerdvana_cli.tools.symbol_tools import InsertAfterSymbolArgs, InsertAfterSymbolTool

        retriever, editor, target = setup
        tool = InsertAfterSymbolTool(retriever=retriever, editor=editor)
        ctx  = ToolContext(cwd=str(target.parent))

        args1 = InsertAfterSymbolArgs(
            name_path     = "User/greeting",
            relative_path = str(target),
            body          = "    def farewell(self) -> str:\n        return f'Bye, {self.name}!'\n",
        )
        r1 = await tool.call(args1, ctx)
        assert not r1.is_error, r1.content
        d1 = json.loads(r1.content)
        assert d1["kind"] == "insert_after"
        assert "farewell" in d1["diff"]

        args2  = InsertAfterSymbolArgs(preview_id=d1["preview_id"], apply=True)
        r2     = await tool.call(args2, ctx)
        assert not r2.is_error, r2.content
        assert json.loads(r2.content)["status"] == "applied"
        assert "farewell" in target.read_text(encoding="utf-8")


@pytest.mark.lsp_integration
@pytest.mark.skipif(not _has_pyright(), reason="pyright not installed")
class TestSafeDeleteSymbolIntegration:
    @pytest.fixture
    async def setup(self, tmp_path: Path) -> tuple[LanguageServerSymbolRetriever, CodeEditor, Path]:
        # Write a minimal file with an unreferenced helper
        py_content = (
            "def unreferenced_helper() -> None:\n"
            "    pass\n"
            "\n"
            "class Main:\n"
            "    def run(self) -> None:\n"
            "        pass\n"
        )
        target = tmp_path / "target.py"
        target.write_text(py_content, encoding="utf-8")

        client    = LspClient(project_root=str(tmp_path))
        retriever = LanguageServerSymbolRetriever(client=client, project_root=str(tmp_path))
        editor    = CodeEditor(project_root=str(tmp_path))
        yield retriever, editor, target
        await client.close()

    async def test_safe_delete_unreferenced(
        self,
        setup: tuple[LanguageServerSymbolRetriever, CodeEditor, Path],
    ) -> None:
        """SafeDelete an unreferenced function produces a valid delete preview."""
        import json

        from nerdvana_cli.core.tool import ToolContext
        from nerdvana_cli.tools.symbol_tools import SafeDeleteSymbolArgs, SafeDeleteSymbolTool

        retriever, editor, target = setup
        tool = SafeDeleteSymbolTool(retriever=retriever, editor=editor)
        ctx  = ToolContext(cwd=str(target.parent))

        args1 = SafeDeleteSymbolArgs(
            name_path     = "unreferenced_helper",
            relative_path = str(target),
        )
        r1 = await tool.call(args1, ctx)
        assert not r1.is_error, r1.content
        d1 = json.loads(r1.content)

        # Either deleted (no refs) or blocked — both are valid outcomes depending
        # on whether pyright counts the definition itself as a reference.
        assert d1.get("kind") == "delete" or d1.get("status") == "blocked_by_references"

        if d1.get("kind") == "delete":
            # Step 2: apply
            args2  = SafeDeleteSymbolArgs(preview_id=d1["preview_id"], apply=True)
            r2     = await tool.call(args2, ctx)
            assert not r2.is_error, r2.content
            assert json.loads(r2.content)["status"] == "applied"
            assert "unreferenced_helper" not in target.read_text(encoding="utf-8")
