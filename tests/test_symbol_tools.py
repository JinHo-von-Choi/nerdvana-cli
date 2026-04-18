"""Unit tests for nerdvana_cli.tools.symbol_tools.

Tests all 5 symbol tool classes using mocked retrievers and editors.
No LSP process required.

작성자: 최진호
작성일: 2026-04-18
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nerdvana_cli.core.code_editor import StalePreviewError, UnknownPreviewError
from nerdvana_cli.core.symbol import LanguageServerSymbol, Location, LspSymbolError
from nerdvana_cli.core.tool import ToolContext
from nerdvana_cli.tools.symbol_tools import (
    FindReferencingSymbolsArgs,
    FindReferencingSymbolsTool,
    FindSymbolArgs,
    FindSymbolTool,
    ReplaceSymbolBodyArgs,
    ReplaceSymbolBodyTool,
    RestartLanguageServerArgs,
    RestartLanguageServerTool,
    SymbolOverviewArgs,
    SymbolOverviewTool,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_sym(name: str, kind: str = "Function", line: int = 1) -> LanguageServerSymbol:
    return LanguageServerSymbol(
        name      = name,
        name_path = name,
        kind      = kind,
        kind_int  = 12,
        location  = Location("/proj/foo.py", line, 0),
    )


def _ctx() -> ToolContext:
    return ToolContext(cwd="/proj")


# ---------------------------------------------------------------------------
# SymbolOverviewTool
# ---------------------------------------------------------------------------


class TestSymbolOverviewTool:
    @pytest.fixture
    def tool(self) -> SymbolOverviewTool:
        retriever = AsyncMock()
        client    = MagicMock()
        return SymbolOverviewTool(retriever=retriever, client=client)

    async def test_returns_symbol_json(self, tool: SymbolOverviewTool) -> None:
        tool._retriever.get_overview.return_value = [_make_sym("foo")]
        args   = SymbolOverviewArgs(relative_path="foo.py")
        result = await tool.call(args, _ctx())
        assert not result.is_error
        data   = json.loads(result.content)
        assert data["symbols"][0]["name"] == "foo"

    async def test_empty_file(self, tool: SymbolOverviewTool) -> None:
        tool._retriever.get_overview.return_value = []
        args   = SymbolOverviewArgs(relative_path="empty.py")
        result = await tool.call(args, _ctx())
        assert not result.is_error
        assert "No symbols found" in result.content

    async def test_lsp_error(self, tool: SymbolOverviewTool) -> None:
        tool._retriever.get_overview.side_effect = LspSymbolError("boom")
        result = await tool.call(SymbolOverviewArgs(relative_path="x.py"), _ctx())
        assert result.is_error
        assert "LSP error" in result.content

    async def test_with_graph_included(self, tool: SymbolOverviewTool) -> None:
        tool._retriever.get_overview.return_value = [_make_sym("foo")]
        args   = SymbolOverviewArgs(relative_path="foo.py", with_graph=True)
        result = await tool.call(args, _ctx())
        data   = json.loads(result.content)
        assert "repo_map" in data

    def test_tool_metadata(self, tool: SymbolOverviewTool) -> None:
        from nerdvana_cli.core.tool import ToolCategory, ToolSideEffect
        assert tool.category    == ToolCategory.SYMBOLIC
        assert tool.side_effects == ToolSideEffect.EXTERNAL
        assert "lsp" in tool.tags
        assert "symbol" in tool.tags
        assert tool.requires_confirmation is False


# ---------------------------------------------------------------------------
# FindSymbolTool
# ---------------------------------------------------------------------------


class TestFindSymbolTool:
    @pytest.fixture
    def tool(self) -> FindSymbolTool:
        retriever = AsyncMock()
        return FindSymbolTool(retriever=retriever)

    async def test_found(self, tool: FindSymbolTool) -> None:
        tool._retriever.find.return_value = [_make_sym("my_func")]
        args   = FindSymbolArgs(name_path="my_func", within_relative_path="foo.py")
        result = await tool.call(args, _ctx())
        data   = json.loads(result.content)
        assert len(data["matches"]) == 1
        assert data["matches"][0]["name"] == "my_func"

    async def test_not_found(self, tool: FindSymbolTool) -> None:
        tool._retriever.find.return_value = []
        args   = FindSymbolArgs(name_path="ghost", within_relative_path="foo.py")
        result = await tool.call(args, _ctx())
        assert "No symbols found" in result.content

    async def test_lsp_error(self, tool: FindSymbolTool) -> None:
        tool._retriever.find.side_effect = LspSymbolError("server crashed")
        args   = FindSymbolArgs(name_path="x", within_relative_path="foo.py")
        result = await tool.call(args, _ctx())
        assert result.is_error


# ---------------------------------------------------------------------------
# FindReferencingSymbolsTool
# ---------------------------------------------------------------------------


class TestFindReferencingSymbolsTool:
    @pytest.fixture
    def tool(self) -> FindReferencingSymbolsTool:
        retriever = AsyncMock()
        return FindReferencingSymbolsTool(retriever=retriever)

    async def test_returns_references(self, tool: FindReferencingSymbolsTool) -> None:
        sym = _make_sym("my_func")
        tool._retriever.find.return_value = [sym]
        tool._retriever.find_references.return_value = [
            Location("/proj/bar.py", 5, 0),
        ]
        args   = FindReferencingSymbolsArgs(name_path="my_func", relative_path="foo.py")
        result = await tool.call(args, _ctx())
        data   = json.loads(result.content)
        assert data["references"][0]["line"] == 5

    async def test_symbol_not_found(self, tool: FindReferencingSymbolsTool) -> None:
        tool._retriever.find.return_value = []
        args   = FindReferencingSymbolsArgs(name_path="missing", relative_path="foo.py")
        result = await tool.call(args, _ctx())
        assert result.is_error

    async def test_no_references(self, tool: FindReferencingSymbolsTool) -> None:
        sym = _make_sym("lonely_func")
        tool._retriever.find.return_value = [sym]
        tool._retriever.find_references.return_value = []
        args   = FindReferencingSymbolsArgs(name_path="lonely_func", relative_path="foo.py")
        result = await tool.call(args, _ctx())
        assert "No references" in result.content


# ---------------------------------------------------------------------------
# RestartLanguageServerTool
# ---------------------------------------------------------------------------


class TestRestartLanguageServerTool:
    @pytest.fixture
    def tool(self) -> RestartLanguageServerTool:
        client = MagicMock()
        client.shutdown_server = AsyncMock()
        client._disabled       = set()
        return RestartLanguageServerTool(client=client)

    async def test_restart_python(self, tool: RestartLanguageServerTool) -> None:
        args   = RestartLanguageServerArgs(language="python")
        result = await tool.call(args, _ctx())
        tool._client.shutdown_server.assert_called_once_with(".py")
        assert not result.is_error
        assert "python" in result.content

    async def test_restart_all(self, tool: RestartLanguageServerTool) -> None:
        args   = RestartLanguageServerArgs(language=None)
        result = await tool.call(args, _ctx())
        assert not result.is_error
        assert "Restarted" in result.content

    async def test_unknown_language(self, tool: RestartLanguageServerTool) -> None:
        args   = RestartLanguageServerArgs(language="cobol")
        result = await tool.call(args, _ctx())
        assert result.is_error
        assert "Unknown language" in result.content

    def test_tool_metadata(self, tool: RestartLanguageServerTool) -> None:
        from nerdvana_cli.core.tool import ToolCategory, ToolSideEffect
        assert tool.category     == ToolCategory.META
        assert tool.side_effects == ToolSideEffect.EXTERNAL
        assert "lsp" in tool.tags


# ---------------------------------------------------------------------------
# ReplaceSymbolBodyTool
# ---------------------------------------------------------------------------


class TestReplaceSymbolBodyTool:
    def _make_tool(
        self,
        tmp_path: Path,
        symbols:  list[LanguageServerSymbol],
        refs:     list[Location] | None = None,
    ) -> ReplaceSymbolBodyTool:
        retriever              = AsyncMock()
        retriever.find         = AsyncMock(return_value=symbols)
        retriever.find_references = AsyncMock(return_value=refs or [])
        retriever._resolve     = lambda p: str(tmp_path / p)

        from nerdvana_cli.core.code_editor import CodeEditor
        editor = CodeEditor(project_root=str(tmp_path))

        return ReplaceSymbolBodyTool(retriever=retriever, editor=editor)

    async def test_step1_generates_preview(self, tmp_path: Path) -> None:
        f = tmp_path / "foo.py"
        f.write_text("def foo():\n    pass\n", encoding="utf-8")
        sym  = LanguageServerSymbol(
            name="foo", name_path="foo", kind="Function", kind_int=12,
            location=Location(str(f), 1, 0),
        )
        tool = self._make_tool(tmp_path, [sym])
        args = ReplaceSymbolBodyArgs(
            name_path="foo", relative_path="foo.py",
            body="def foo():\n    return 42\n",
        )
        result = await tool.call(args, _ctx())
        assert not result.is_error
        data   = json.loads(result.content)
        assert "preview_id" in data
        assert "diff" in data
        assert data["kind"] == "replace_body"

    async def test_step2_apply_success(self, tmp_path: Path) -> None:
        f = tmp_path / "bar.py"
        f.write_text("def bar():\n    pass\n", encoding="utf-8")
        sym  = LanguageServerSymbol(
            name="bar", name_path="bar", kind="Function", kind_int=12,
            location=Location(str(f), 1, 0),
        )
        tool = self._make_tool(tmp_path, [sym])
        # Step 1
        args1 = ReplaceSymbolBodyArgs(
            name_path="bar", relative_path="bar.py",
            body="def bar():\n    return 99\n",
        )
        r1   = await tool.call(args1, _ctx())
        d1   = json.loads(r1.content)
        pid  = d1["preview_id"]

        # Step 2
        args2  = ReplaceSymbolBodyArgs(preview_id=pid, apply=True)
        result = await tool.call(args2, _ctx())
        data   = json.loads(result.content)
        assert data["status"] == "applied"

    async def test_step2_stale(self, tmp_path: Path) -> None:
        f = tmp_path / "stale.py"
        f.write_text("def stale():\n    pass\n", encoding="utf-8")
        sym  = LanguageServerSymbol(
            name="stale", name_path="stale", kind="Function", kind_int=12,
            location=Location(str(f), 1, 0),
        )
        tool = self._make_tool(tmp_path, [sym])
        args1 = ReplaceSymbolBodyArgs(
            name_path="stale", relative_path="stale.py",
            body="def stale(): return 0\n",
        )
        r1  = await tool.call(args1, _ctx())
        pid = json.loads(r1.content)["preview_id"]

        # Mutate file
        f.write_text("def stale():\n    return 777\n", encoding="utf-8")

        args2  = ReplaceSymbolBodyArgs(preview_id=pid, apply=True)
        result = await tool.call(args2, _ctx())
        data   = json.loads(result.content)
        assert data["status"] == "STALE"

    async def test_invalid_args(self, tmp_path: Path) -> None:
        tool   = self._make_tool(tmp_path, [])
        args   = ReplaceSymbolBodyArgs()   # no fields set
        result = await tool.call(args, _ctx())
        assert result.is_error

    def test_tool_metadata(self, tmp_path: Path) -> None:
        tool = self._make_tool(tmp_path, [])
        from nerdvana_cli.core.tool import ToolCategory, ToolSideEffect
        assert tool.category      == ToolCategory.WRITE
        assert tool.side_effects  == ToolSideEffect.FILESYSTEM
        assert "edit" in tool.tags
        assert tool.requires_confirmation is True
