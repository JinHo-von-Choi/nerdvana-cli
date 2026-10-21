"""Tests for Parism MCP client wrapper."""

import asyncio
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


class _Stdout:
    def __init__(self, lines: list[bytes]) -> None:
        self._lines = list(lines)

    async def readline(self) -> bytes:
        return self._lines.pop(0) if self._lines else b""


class _Process:
    def __init__(self, lines: list[bytes]) -> None:
        self.stdout = _Stdout(lines)


class TestReadLoop:
    @pytest.mark.asyncio
    async def test_bad_line_is_skipped_and_the_answer_after_it_arrives(self):
        client = ParismClient()
        loop   = asyncio.get_running_loop()
        answer = loop.create_future()
        client._pending[1] = answer
        client._process = _Process([b"not json\n", b'[1, 2]\n', b'{"jsonrpc": "2.0", "id": 1, "result": {"ok": true}}\n'])

        await client._read_loop()

        assert answer.result() == {"ok": True}

    @pytest.mark.asyncio
    async def test_requests_still_waiting_fail_when_the_output_ends(self):
        client = ParismClient()
        waiting = asyncio.get_running_loop().create_future()
        client._pending[7] = waiting
        client._process = _Process([])

        await client._read_loop()

        with pytest.raises(RuntimeError, match="stopped before answering"):
            waiting.result()
        assert client._pending == {}
