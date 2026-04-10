"""Tests for path traversal prevention in file tools."""
import pytest

from nerdvana_cli.core.tool import ToolContext
from nerdvana_cli.tools.file_tools import (
    FileEditArgs,
    FileEditTool,
    FileReadArgs,
    FileReadTool,
    FileWriteArgs,
    FileWriteTool,
)


@pytest.fixture
def file_ctx(tmp_path):
    (tmp_path / "safe.txt").write_text("safe content")
    return ToolContext(cwd=str(tmp_path))


class TestFileReadTraversal:
    @pytest.mark.asyncio
    async def test_relative_traversal_blocked(self, file_ctx):
        tool = FileReadTool()
        result = await tool.call(
            FileReadArgs(path="../../etc/passwd"), file_ctx, can_use_tool=None
        )
        assert result.is_error
        assert "outside" in result.content.lower() or "traversal" in result.content.lower()

    @pytest.mark.asyncio
    async def test_absolute_path_outside_cwd_blocked(self, file_ctx):
        tool = FileReadTool()
        result = await tool.call(
            FileReadArgs(path="/etc/passwd"), file_ctx, can_use_tool=None
        )
        assert result.is_error

    @pytest.mark.asyncio
    async def test_safe_path_allowed(self, file_ctx):
        tool = FileReadTool()
        result = await tool.call(
            FileReadArgs(path="safe.txt"), file_ctx, can_use_tool=None
        )
        assert not result.is_error
        assert "safe content" in result.content


class TestFileWriteTraversal:
    @pytest.mark.asyncio
    async def test_traversal_blocked(self, file_ctx):
        tool = FileWriteTool()
        result = await tool.call(
            FileWriteArgs(path="../escape.txt", content="hacked"), file_ctx, can_use_tool=None
        )
        assert result.is_error

    @pytest.mark.asyncio
    async def test_absolute_path_blocked(self, file_ctx):
        tool = FileWriteTool()
        result = await tool.call(
            FileWriteArgs(path="/tmp/escape.txt", content="hacked"), file_ctx, can_use_tool=None
        )
        assert result.is_error


class TestFileEditTraversal:
    @pytest.mark.asyncio
    async def test_traversal_blocked(self, file_ctx):
        tool = FileEditTool()
        result = await tool.call(
            FileEditArgs(path="../../etc/hosts", old_string="x", new_string="y"),
            file_ctx, can_use_tool=None,
        )
        assert result.is_error
