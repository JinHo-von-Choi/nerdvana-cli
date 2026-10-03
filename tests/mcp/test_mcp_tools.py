"""Tests for the MCP tool adapter."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from nerdvana_cli.core.tool import ToolContext
from nerdvana_cli.mcp.client import McpClient
from nerdvana_cli.mcp.config import McpServerConfig
from nerdvana_cli.mcp.tools import McpToolAdapter


def _make_client(name: str = "test-server") -> McpClient:
    config = McpServerConfig(name=name, command="echo")
    client = McpClient(config)
    client._connected = True
    return client


def _make_tool_def(
    name: str = "my_tool",
    description: str = "A test tool",
) -> dict:
    return {
        "name": name,
        "description": description,
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
            },
            "required": ["query"],
        },
    }


class TestMcpToolAdapterNameFormat:
    """Tool name follows mcp__{server}__{tool} format with normalization."""

    def test_simple_names(self):
        client  = _make_client("my-server")
        adapter = McpToolAdapter("my-server", _make_tool_def("do_thing"), client)
        assert adapter.name == "mcp__my_server__do_thing"

    def test_dashes_normalized(self):
        client  = _make_client("brave-search")
        adapter = McpToolAdapter("brave-search", _make_tool_def("web-search"), client)
        assert adapter.name == "mcp__brave_search__web_search"

    def test_dots_normalized(self):
        client  = _make_client("api.server")
        adapter = McpToolAdapter("api.server", _make_tool_def("run.query"), client)
        assert adapter.name == "mcp__api_server__run_query"


class TestMcpToolAdapterPrompt:
    """prompt() returns description and input schema."""

    def test_prompt_contains_description_and_schema(self):
        client  = _make_client()
        adapter = McpToolAdapter("srv", _make_tool_def(), client)
        prompt  = adapter.prompt()

        assert "mcp__srv__my_tool" in prompt
        assert "A test tool" in prompt
        assert "query" in prompt

    def test_prompt_without_schema(self):
        client  = _make_client()
        tool_def = {"name": "simple", "description": "Simple tool"}
        adapter = McpToolAdapter("srv", tool_def, client)
        prompt  = adapter.prompt()

        assert "mcp__srv__simple" in prompt
        assert "Simple tool" in prompt


class TestMcpToolAdapterCall:
    """call() forwards to client.call_tool() and extracts text content."""

    @pytest.mark.asyncio
    async def test_call_forward_and_extract_text(self):
        client  = _make_client()
        adapter = McpToolAdapter("srv", _make_tool_def(), client)
        ctx     = ToolContext()

        client.call_tool = AsyncMock(return_value={
            "content": [
                {"type": "text", "text": "Line 1"},
                {"type": "text", "text": "Line 2"},
            ],
            "isError": False,
        })

        result = await adapter.call({"query": "test"}, ctx, None)

        client.call_tool.assert_awaited_once_with("my_tool", {"query": "test"})
        assert result.content == "Line 1\nLine 2"
        assert result.is_error is False

    @pytest.mark.asyncio
    async def test_call_single_text_content(self):
        client  = _make_client()
        adapter = McpToolAdapter("srv", _make_tool_def(), client)
        ctx     = ToolContext()

        client.call_tool = AsyncMock(return_value={
            "content": [{"type": "text", "text": "Only result"}],
            "isError": False,
        })

        result = await adapter.call({"query": "q"}, ctx, None)
        assert result.content == "Only result"


class TestMcpToolAdapterErrorHandling:
    """Error scenarios return ToolResult with is_error=True."""

    @pytest.mark.asyncio
    async def test_runtime_error_from_client(self):
        client  = _make_client()
        adapter = McpToolAdapter("srv", _make_tool_def(), client)
        ctx     = ToolContext()

        client.call_tool = AsyncMock(side_effect=RuntimeError("Connection lost"))

        result = await adapter.call({"query": "test"}, ctx, None)
        assert result.is_error is True
        assert "Connection lost" in result.content

    @pytest.mark.asyncio
    async def test_server_reports_error(self):
        client  = _make_client()
        adapter = McpToolAdapter("srv", _make_tool_def(), client)
        ctx     = ToolContext()

        client.call_tool = AsyncMock(return_value={
            "content": [{"type": "text", "text": "Tool failed"}],
            "isError": True,
        })

        result = await adapter.call({"query": "test"}, ctx, None)
        assert result.is_error is True
        assert "Tool failed" in result.content
