"""Tests for Parism MCP client wrapper."""

from unittest.mock import AsyncMock

import pytest

from nerdvana_cli.tools.parism_client import ParismClient


class TestParismClient:
    @pytest.mark.asyncio
    async def test_connect_starts_process(self):
        client = ParismClient()
        assert not client.is_connected

    @pytest.mark.asyncio
    async def test_run_returns_structured_output(self):
        client = ParismClient()
        # Mock the MCP call
        mock_result = {
            "ok": True,
            "exitCode": 0,
            "cmd": "echo",
            "args": ["hello"],
            "duration_ms": 5,
            "stdout": {"raw": "hello\n", "parsed": None},
            "stderr": {"raw": "", "parsed": None},
        }
        client._call_tool = AsyncMock(return_value=mock_result)
        client._connected = True

        result = await client.run("echo", ["hello"])
        assert result["ok"] is True
        assert result["stdout"]["raw"] == "hello\n"

    @pytest.mark.asyncio
    async def test_run_when_not_connected_raises(self):
        client = ParismClient()
        with pytest.raises(RuntimeError, match="not connected"):
            await client.run("echo", ["hello"])
