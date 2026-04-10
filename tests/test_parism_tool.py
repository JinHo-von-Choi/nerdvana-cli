"""Tests for ParismTool."""

from unittest.mock import AsyncMock

import pytest

from nerdvana_cli.core.tool import ToolContext
from nerdvana_cli.tools.parism_tool import ParismArgs, ParismTool


class TestParismArgs:
    def test_parse_all_fields(self):
        tool = ParismTool()
        raw  = {"cmd": "ls", "args": ["-la"], "cwd": "/tmp"}
        args = tool.parse_args(raw)
        assert isinstance(args, ParismArgs)
        assert args.cmd == "ls"
        assert args.args == ["-la"]

    def test_parse_required_only(self):
        tool = ParismTool()
        raw  = {"cmd": "pwd"}
        args = tool.parse_args(raw)
        assert args.cmd == "pwd"
        assert args.args == []
        assert args.format == "json"


class TestParismToolExecution:
    @pytest.mark.asyncio
    async def test_call_returns_formatted_output(self):
        tool          = ParismTool()
        tool._client  = AsyncMock()
        tool._client.is_connected = True
        tool._client.run = AsyncMock(return_value={
            "ok": True,
            "exitCode": 0,
            "cmd": "pwd",
            "args": [],
            "duration_ms": 3,
            "stdout": {"raw": "/home/user\n", "parsed": {"path": "/home/user"}},
            "stderr": {"raw": "", "parsed": None},
        })

        context = ToolContext(cwd="/home/user")
        args    = ParismArgs(cmd="pwd")
        result  = await tool.call(args, context)
        assert not result.is_error
        assert "/home/user" in result.content

    @pytest.mark.asyncio
    async def test_call_guard_error(self):
        tool          = ParismTool()
        tool._client  = AsyncMock()
        tool._client.is_connected = True
        tool._client.run = AsyncMock(return_value={
            "ok": False,
            "guard_error": {
                "reason": "command_not_allowed",
                "message": "Command 'rm' is not in the allowed list",
            },
        })

        context = ToolContext(cwd="/tmp")
        args    = ParismArgs(cmd="rm", args=["-rf", "/"])
        result  = await tool.call(args, context)
        assert result.is_error
        assert "not in the allowed list" in result.content
