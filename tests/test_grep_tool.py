"""Tests for GrepTool result counting accuracy."""
import pytest

from nerdvana_cli.core.tool import ToolContext
from nerdvana_cli.tools.search_tools import GrepArgs, GrepTool


@pytest.fixture
def grep_fixture(tmp_path):
    (tmp_path / "a.py").write_text("hello world\ngoodbye world\n")
    (tmp_path / "b.py").write_text("hello again\n")
    (tmp_path / "c.txt").write_text("no match here\n")
    ctx = ToolContext(cwd=str(tmp_path))
    tool = GrepTool()
    return tool, ctx


class TestGrepToolCounting:
    @pytest.mark.asyncio
    async def test_file_count_is_files_not_matches(self, grep_fixture):
        tool, ctx = grep_fixture
        result = await tool.call(GrepArgs(pattern="hello"), ctx, can_use_tool=None)
        assert "2 match(es)" in result.content
        assert "2 file(s)" in result.content

    @pytest.mark.asyncio
    async def test_multiple_matches_in_one_file(self, grep_fixture):
        tool, ctx = grep_fixture
        result = await tool.call(GrepArgs(pattern="world"), ctx, can_use_tool=None)
        assert "2 match(es)" in result.content
        assert "1 file(s)" in result.content

    @pytest.mark.asyncio
    async def test_no_duplicate_results(self, grep_fixture):
        tool, ctx = grep_fixture
        result = await tool.call(GrepArgs(pattern="hello"), ctx, can_use_tool=None)
        lines = [line for line in result.content.split("\n") if "hello" in line.lower()]
        assert len(lines) == 2
