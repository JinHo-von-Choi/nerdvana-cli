"""Tests for path traversal prevention in GlobTool and GrepTool."""
import pytest

from nerdvana_cli.core.tool import ToolContext
from nerdvana_cli.tools.search_tools import (
    GlobArgs,
    GlobTool,
    GrepArgs,
    GrepTool,
)


@pytest.fixture
def search_ctx(tmp_path):
    (tmp_path / "test.txt").write_text("hello world")
    return ToolContext(cwd=str(tmp_path))


class TestGlobPathTraversal:
    @pytest.mark.asyncio
    async def test_relative_traversal_blocked(self, search_ctx):
        tool = GlobTool()
        args = GlobArgs(pattern="*.py", path="../../etc")
        result = await tool.call(args, search_ctx, can_use_tool=None)
        assert result.is_error
        assert "traversal" in result.content.lower() or "outside" in result.content.lower()

    @pytest.mark.asyncio
    async def test_absolute_path_blocked(self, search_ctx):
        tool = GlobTool()
        args = GlobArgs(pattern="*", path="/etc")
        result = await tool.call(args, search_ctx, can_use_tool=None)
        assert result.is_error
        assert "absolute" in result.content.lower()

    @pytest.mark.asyncio
    async def test_normal_path_works(self, search_ctx):
        tool = GlobTool()
        args = GlobArgs(pattern="*.txt", path=".")
        result = await tool.call(args, search_ctx, can_use_tool=None)
        assert not result.is_error
        assert "test.txt" in result.content


class TestGrepPathTraversal:
    @pytest.mark.asyncio
    async def test_relative_traversal_blocked(self, search_ctx):
        tool = GrepTool()
        args = GrepArgs(pattern="password", path="../../etc")
        result = await tool.call(args, search_ctx, can_use_tool=None)
        assert result.is_error
        assert "traversal" in result.content.lower() or "outside" in result.content.lower()

    @pytest.mark.asyncio
    async def test_absolute_path_blocked(self, search_ctx):
        tool = GrepTool()
        args = GrepArgs(pattern="root", path="/etc")
        result = await tool.call(args, search_ctx, can_use_tool=None)
        assert result.is_error
        assert "absolute" in result.content.lower()

    @pytest.mark.asyncio
    async def test_normal_search_works(self, search_ctx):
        tool = GrepTool()
        args = GrepArgs(pattern="hello", path=".")
        result = await tool.call(args, search_ctx, can_use_tool=None)
        assert not result.is_error
        assert "hello" in result.content
