"""Integration test — _run_single_tool correctly parses args and executes."""

import pytest

from nerdvana_cli.core.tool import ToolContext
from nerdvana_cli.tools.bash_tool import BashTool


@pytest.mark.asyncio
async def test_run_single_tool_with_dict_input():
    tool = BashTool()
    context = ToolContext(cwd="/tmp")
    tool_use_input = {"command": "echo hello", "description": "test echo"}

    parsed = tool.parse_args(tool_use_input)
    assert parsed.command == "echo hello"
    assert parsed.timeout == 120

    result = await tool.call(parsed, context, can_use_tool=None)
    assert "hello" in result.content
    assert not result.is_error


@pytest.mark.asyncio
async def test_run_single_tool_invalid_input():
    tool = BashTool()
    with pytest.raises(TypeError):
        tool.parse_args({})
