"""Tests for the MCP stdio client."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from nerdvana_cli.mcp.client import McpClient
from nerdvana_cli.mcp.config import McpServerConfig


def _make_config(name: str = "test-server") -> McpServerConfig:
    return McpServerConfig(
        name=name,
        transport="stdio",
        command="echo",
        args=[],
    )


class TestMcpClientNotConnected:
    """Operations on a not-yet-connected client raise RuntimeError."""

    @pytest.mark.asyncio
    async def test_list_tools_raises_when_not_connected(self):
        client = McpClient(_make_config())
        with pytest.raises(RuntimeError, match="not connected"):
            await client.list_tools()

    @pytest.mark.asyncio
    async def test_call_tool_raises_when_not_connected(self):
        client = McpClient(_make_config())
        with pytest.raises(RuntimeError, match="not connected"):
            await client.call_tool("some_tool", {"arg": "val"})

    @pytest.mark.asyncio
    async def test_list_resources_raises_when_not_connected(self):
        client = McpClient(_make_config())
        with pytest.raises(RuntimeError, match="not connected"):
            await client.list_resources()


def _fake_server_config() -> McpServerConfig:
    return McpServerConfig(
        name="fake",
        transport="stdio",
        command=sys.executable,
        args=[str(Path(__file__).parent / "mcp" / "fake_server.py")],
    )


class TestMcpClientListTools:
    """list_tools() requests tools/list and returns the tools array."""

    @pytest.mark.asyncio
    async def test_list_tools_returns_tools(self):
        client = McpClient(_fake_server_config())
        await client.connect()
        try:
            tools = await client.list_tools()
        finally:
            await client.disconnect()

        assert [tool["name"] for tool in tools] == ["echo", "fail", "ask", "list_count"]
        assert tools[0]["description"] == "Echo the arguments"
        assert tools[0]["inputSchema"]["properties"] == {"text": {"type": "string"}}

    @pytest.mark.asyncio
    async def test_second_listing_is_served_from_the_cache_while_the_server_ttl_holds(self):
        client = McpClient(_fake_server_config())
        await client.connect()
        try:
            await client.list_tools()
            await client.list_tools()
            served = await client.call_tool("list_count")
        finally:
            await client.disconnect()

        assert served["content"][0]["text"] == "1"


class TestMcpClientCallTool:
    """call_tool() sends tools/call and returns the result."""

    @pytest.mark.asyncio
    async def test_call_tool_returns_result(self):
        client = McpClient(_fake_server_config())
        await client.connect()
        try:
            result = await client.call_tool("echo", {"text": "Hello from tool"})
        finally:
            await client.disconnect()

        assert result["isError"] is False
        assert json.loads(result["content"][0]["text"]) == {"text": "Hello from tool"}

    @pytest.mark.asyncio
    async def test_an_error_result_keeps_its_is_error_flag(self):
        client = McpClient(_fake_server_config())
        await client.connect()
        try:
            result = await client.call_tool("fail")
        finally:
            await client.disconnect()

        assert result["isError"] is True
        assert result["content"][0]["text"] == "it failed"


class TestMcpClientDisconnected:
    """After disconnect, operations raise RuntimeError."""

    @pytest.mark.asyncio
    async def test_operations_fail_after_disconnect(self):
        client = McpClient(_make_config())
        client._connected = True

        await client.disconnect()
        assert not client.connected

        with pytest.raises(RuntimeError, match="not connected"):
            await client.list_tools()
