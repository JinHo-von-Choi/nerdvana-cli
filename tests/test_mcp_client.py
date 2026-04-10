"""Tests for the MCP stdio client."""

from __future__ import annotations

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


class TestMcpClientListTools:
    """list_tools() sends tools/list and returns the tools array."""

    @pytest.mark.asyncio
    async def test_list_tools_returns_tools(self):
        client = McpClient(_make_config())
        client._connected = True

        expected_tools = [
            {"name": "tool_a", "description": "Does A"},
            {"name": "tool_b", "description": "Does B"},
        ]

        async def mock_send_request(method, params):
            if method == "tools/list":
                return {"tools": expected_tools}
            return {}

        client._send_request = mock_send_request

        tools = await client.list_tools()
        assert tools == expected_tools
        assert len(tools) == 2


class TestMcpClientCallTool:
    """call_tool() sends tools/call and returns the result."""

    @pytest.mark.asyncio
    async def test_call_tool_returns_result(self):
        client = McpClient(_make_config())
        client._connected = True

        expected_result = {
            "content": [{"type": "text", "text": "Hello from tool"}],
            "isError": False,
        }

        async def mock_send_request(method, params):
            if method == "tools/call":
                assert params["name"] == "my_tool"
                assert params["arguments"] == {"query": "test"}
                return expected_result
            return {}

        client._send_request = mock_send_request

        result = await client.call_tool("my_tool", {"query": "test"})
        assert result == expected_result
        assert result["content"][0]["text"] == "Hello from tool"


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
