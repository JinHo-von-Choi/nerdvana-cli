"""Tests for LSP tool classes."""
from unittest.mock import AsyncMock, MagicMock

import pytest

from nerdvana_cli.core.tool import ToolContext
from nerdvana_cli.tools.lsp import (
    LspDiagnosticsTool,
    LspFindReferencesTool,
    LspGotoDefinitionTool,
    LspRenameTool,
)


def _ctx() -> ToolContext:
    ctx = MagicMock(spec=ToolContext)
    ctx.cwd = "/tmp"
    return ctx


@pytest.mark.asyncio
async def test_diagnostics_tool_formats_output():
    mock_client = MagicMock()
    mock_client.diagnostics = AsyncMock(return_value=[
        {"line": 5, "col": 2, "severity": "error", "message": "Undefined name 'x'"},
    ])
    tool = LspDiagnosticsTool(client=mock_client)
    result = await tool.call(tool.args_class(file_path="src/main.py"), _ctx(), None)
    assert not result.is_error
    assert "line 5" in result.content
    assert "Undefined name 'x'" in result.content


@pytest.mark.asyncio
async def test_diagnostics_tool_no_issues():
    mock_client = MagicMock()
    mock_client.diagnostics = AsyncMock(return_value=[])
    tool = LspDiagnosticsTool(client=mock_client)
    result = await tool.call(tool.args_class(file_path="src/ok.py"), _ctx(), None)
    assert not result.is_error
    assert "No issues" in result.content


@pytest.mark.asyncio
async def test_goto_definition_tool():
    mock_client = MagicMock()
    mock_client.goto_definition = AsyncMock(return_value={
        "file": "src/utils.py", "line": 10, "col": 4,
    })
    tool = LspGotoDefinitionTool(client=mock_client)
    result = await tool.call(
        tool.args_class(file_path="src/main.py", line=3, symbol="helper"), _ctx(), None,
    )
    assert not result.is_error
    assert "src/utils.py" in result.content
    assert "10" in result.content


@pytest.mark.asyncio
async def test_goto_definition_not_found():
    mock_client = MagicMock()
    mock_client.goto_definition = AsyncMock(return_value=None)
    tool = LspGotoDefinitionTool(client=mock_client)
    result = await tool.call(
        tool.args_class(file_path="src/main.py", line=3, symbol="ghost"), _ctx(), None,
    )
    assert not result.is_error
    assert "No definition" in result.content


@pytest.mark.asyncio
async def test_find_references_tool():
    mock_client = MagicMock()
    mock_client.find_references = AsyncMock(return_value=[
        {"file": "a.py", "line": 1, "col": 0},
        {"file": "b.py", "line": 5, "col": 2},
    ])
    tool = LspFindReferencesTool(client=mock_client)
    result = await tool.call(
        tool.args_class(file_path="src/x.py", line=1, symbol="MyClass"), _ctx(), None,
    )
    assert not result.is_error
    assert "a.py" in result.content
    assert "b.py" in result.content


@pytest.mark.asyncio
async def test_rename_tool():
    mock_client = MagicMock()
    mock_client.rename = AsyncMock(return_value={
        "changed_files": ["src/a.py", "src/b.py"],
        "diffs": [],
    })
    tool = LspRenameTool(client=mock_client)
    result = await tool.call(
        tool.args_class(
            file_path="src/a.py", line=1, symbol="old_name", new_name="new_name",
        ),
        _ctx(),
        None,
    )
    assert not result.is_error
    assert "src/a.py" in result.content
    assert "src/b.py" in result.content


@pytest.mark.asyncio
async def test_diagnostics_tool_lsp_error():
    mock_client = MagicMock()
    mock_client.diagnostics = AsyncMock(side_effect=RuntimeError("boom"))
    tool = LspDiagnosticsTool(client=mock_client)
    result = await tool.call(tool.args_class(file_path="src/x.py"), _ctx(), None)
    assert result.is_error
    assert "boom" in result.content
