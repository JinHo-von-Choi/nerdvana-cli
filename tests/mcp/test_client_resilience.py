"""Resilience checks for the stdio MCP client.

Covers two failure modes that used to leave the client wedged:
oversized stdout lines killing the connection while `connected` stayed True,
and a failed handshake leaking the child process plus its tasks.

The server on the other end is a real subprocess (tests/mcp/raw_server.py), so the checks run through the
same SDK client and transport the application uses.
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path
from typing import Any

import anyio
import pytest

from nerdvana_cli.mcp import client as client_mod
from nerdvana_cli.mcp import transport
from nerdvana_cli.mcp.client import _MAX_RESPONSE_BYTES, McpClient
from nerdvana_cli.mcp.config import McpServerConfig

_READ_LIMIT = 4096
_RAW_SERVER = str(Path(__file__).parent / "raw_server.py")


def _stdio_config(mode: str, **env: str) -> McpServerConfig:
    return McpServerConfig(
        name="resilience-server",
        transport="stdio",
        command=sys.executable,
        args=[_RAW_SERVER, mode],
        env=env,
    )


class TestOversizedLine:
    """A line past the limit must kill the connection, not wedge it."""

    @pytest.mark.asyncio
    async def test_oversized_line_marks_connection_dead(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(client_mod, "_MAX_RESPONSE_BYTES", _READ_LIMIT)
        client = McpClient(_stdio_config("oversize"))
        await client.connect()

        with pytest.raises(RuntimeError):
            await asyncio.wait_for(client.list_tools(), timeout=5.0)

        assert client.connected is False
        await client.disconnect()

    @pytest.mark.asyncio
    async def test_in_flight_request_fails_without_waiting_for_timeout(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(client_mod, "_MAX_RESPONSE_BYTES", _READ_LIMIT)
        client = McpClient(_stdio_config("oversize"))
        await client.connect()

        started = time.monotonic()
        with pytest.raises(RuntimeError):
            await asyncio.wait_for(client.list_tools(), timeout=5.0)

        elapsed = time.monotonic() - started
        assert elapsed < 5.0, f"request waited {elapsed:.2f}s instead of failing"
        assert elapsed < client_mod._REQUEST_TIMEOUT
        await client.disconnect()

    @pytest.mark.asyncio
    async def test_later_request_fails_immediately(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(client_mod, "_MAX_RESPONSE_BYTES", _READ_LIMIT)
        client = McpClient(_stdio_config("oversize"))
        await client.connect()
        with pytest.raises(RuntimeError):
            await asyncio.wait_for(client.list_tools(), timeout=5.0)

        started = time.monotonic()
        with pytest.raises(RuntimeError, match="not connected"):
            await client.call_tool("anything", {})

        assert time.monotonic() - started < 1.0
        await client.disconnect()


class TestNormalTraffic:
    """The size guard must not break ordinary responses."""

    @pytest.mark.asyncio
    async def test_normal_response_is_still_delivered(self) -> None:
        client = McpClient(_stdio_config("ok", DESCRIPTION_BYTES="10"))
        await client.connect()

        tools = await asyncio.wait_for(client.list_tools(), timeout=5.0)

        assert [tool["name"] for tool in tools] == ["big"]
        assert tools[0]["description"] == "y" * 10
        assert client.connected is True
        await client.disconnect()

    @pytest.mark.asyncio
    async def test_large_but_allowed_line_is_delivered(self) -> None:
        """A line well past the 64 KiB read chunk still parses under the 10 MB limit."""
        client = McpClient(_stdio_config("ok"))
        await client.connect()

        tools = await asyncio.wait_for(client.list_tools(), timeout=10.0)

        assert tools[0]["description"] == "y" * (200 * 1024)
        assert client.connected is True
        await client.disconnect()

    def test_limit_is_ten_megabytes(self) -> None:
        assert _MAX_RESPONSE_BYTES == 10 * 1024 * 1024


class TestConnectFailureCleanup:
    """A handshake that never completes must not leak the child process."""

    @pytest.mark.asyncio
    async def test_failed_connect_reaps_subprocess(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(client_mod, "_REQUEST_TIMEOUT", 0.5)
        monkeypatch.setattr(transport, "_EXIT_GRACE_SECONDS", 0.1)

        spawned: list[Any] = []
        real_open = anyio.open_process

        async def spy_open(*args: Any, **kwargs: Any) -> Any:
            process = await real_open(*args, **kwargs)
            spawned.append(process)
            return process

        monkeypatch.setattr(anyio, "open_process", spy_open)

        # A server that never answers on stdout, so the handshake can only time out.
        client = McpClient(_stdio_config("hang"))

        with pytest.raises(RuntimeError, match="timed out"):
            await client.connect()

        assert spawned, "no subprocess was created"
        pid = spawned[0].pid
        assert spawned[0].returncode is not None
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)
        assert client.connected is False

    @pytest.mark.asyncio
    async def test_failed_connect_leaves_no_background_task(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(client_mod, "_REQUEST_TIMEOUT", 0.5)
        monkeypatch.setattr(transport, "_EXIT_GRACE_SECONDS", 0.1)
        before = asyncio.all_tasks()

        client = McpClient(_stdio_config("hang"))
        with pytest.raises(RuntimeError, match="timed out"):
            await client.connect()

        assert client._runner is None
        assert [task for task in asyncio.all_tasks() - before if not task.done()] == []

    @pytest.mark.asyncio
    async def test_missing_command_reports_it(self) -> None:
        config = McpServerConfig(name="absent", transport="stdio", command="/nonexistent/nerdvana-mcp-server")
        with pytest.raises(RuntimeError, match="command not found"):
            await McpClient(config).connect()
