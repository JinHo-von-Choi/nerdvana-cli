"""Resilience checks for the stdio MCP client.

Covers two failure modes that used to leave the client wedged:
oversized stdout lines killing the reader while `connected` stayed True,
and a failed handshake leaking the child process plus its reader task.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import pytest

from nerdvana_cli.mcp import client as client_mod
from nerdvana_cli.mcp.client import _MAX_RESPONSE_BYTES, McpClient
from nerdvana_cli.mcp.config import McpServerConfig

_READ_LIMIT = 4096


class _FakeStdin:
    """Minimal stdin double that records what the client wrote."""

    def __init__(self) -> None:
        self.chunks: list[bytes] = []
        self.closed = False

    def write(self, data: bytes) -> None:
        self.chunks.append(data)

    async def drain(self) -> None:
        return None

    def close(self) -> None:
        self.closed = True


class _FakeProcess:
    """Subprocess double wired to a real StreamReader for stdout."""

    def __init__(self, limit: int) -> None:
        self.stdin      = _FakeStdin()
        self.stdout     = asyncio.StreamReader(limit=limit)
        self.returncode: int | None = None
        self.terminated = False

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = -15

    def kill(self) -> None:
        self.returncode = -9

    async def wait(self) -> int:
        return self.returncode if self.returncode is not None else 0


def _stdio_config(command: str = "cat", args: list[str] | None = None) -> McpServerConfig:
    return McpServerConfig(
        name="resilience-server",
        transport="stdio",
        command=command,
        args=args or [],
    )


def _start_reader(limit: int = _READ_LIMIT) -> tuple[McpClient, _FakeProcess]:
    """Build a connected client whose reader loop is running against a fake process."""
    client              = McpClient(_stdio_config())
    process             = _FakeProcess(limit)
    client._process     = process  # type: ignore[assignment]
    client._connected   = True
    client._reader_task = asyncio.create_task(client._read_loop())
    return client, process


async def _drain_reader(client: McpClient) -> None:
    task = client._reader_task
    assert task is not None
    await asyncio.wait_for(task, timeout=2.0)


def _oversized_line(limit: int) -> bytes:
    payload = json.dumps({"jsonrpc": "2.0", "id": 1, "result": {"blob": "x" * (limit * 3)}})
    return payload.encode("utf-8") + b"\n"


class TestOversizedLine:
    """A line past the StreamReader limit must kill the connection, not wedge it."""

    @pytest.mark.asyncio
    async def test_oversized_line_marks_connection_dead(self) -> None:
        client, process = _start_reader()

        process.stdout.feed_data(_oversized_line(_READ_LIMIT))
        await _drain_reader(client)

        assert client.connected is False

    @pytest.mark.asyncio
    async def test_in_flight_request_fails_without_waiting_for_timeout(self) -> None:
        client, process = _start_reader()

        request = asyncio.create_task(client.list_tools())
        await asyncio.sleep(0)  # let the request register in _pending

        started = time.monotonic()
        process.stdout.feed_data(_oversized_line(_READ_LIMIT))

        with pytest.raises(RuntimeError):
            await asyncio.wait_for(request, timeout=2.0)

        elapsed = time.monotonic() - started
        assert elapsed < 1.0, f"request waited {elapsed:.2f}s instead of failing immediately"
        assert elapsed < client_mod._REQUEST_TIMEOUT

    @pytest.mark.asyncio
    async def test_later_request_fails_immediately(self) -> None:
        client, process = _start_reader()

        process.stdout.feed_data(_oversized_line(_READ_LIMIT))
        await _drain_reader(client)

        started = time.monotonic()
        with pytest.raises(RuntimeError):
            await client.call_tool("anything", {})
        elapsed = time.monotonic() - started

        assert elapsed < 1.0, f"request waited {elapsed:.2f}s instead of failing immediately"


class TestNormalTraffic:
    """The size guard must not break ordinary responses."""

    @pytest.mark.asyncio
    async def test_normal_response_is_still_delivered(self) -> None:
        client, process = _start_reader()

        request = asyncio.create_task(client.list_tools())
        await asyncio.sleep(0)

        response = {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {"tools": [{"name": "echo", "description": "echoes"}]},
        }
        process.stdout.feed_data(json.dumps(response).encode("utf-8") + b"\n")

        tools = await asyncio.wait_for(request, timeout=2.0)

        assert tools == [{"name": "echo", "description": "echoes"}]
        assert client.connected is True

        process.stdout.feed_eof()
        await _drain_reader(client)

    @pytest.mark.asyncio
    async def test_large_but_allowed_line_is_delivered(self) -> None:
        """A line well past the 64 KiB asyncio default still parses under the raised limit."""
        client, process = _start_reader(limit=_MAX_RESPONSE_BYTES)

        request = asyncio.create_task(client.list_tools())
        await asyncio.sleep(0)

        blob     = "y" * (200 * 1024)
        response = {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {"tools": [{"name": "big", "description": blob}]},
        }
        process.stdout.feed_data(json.dumps(response).encode("utf-8") + b"\n")

        tools = await asyncio.wait_for(request, timeout=5.0)

        assert tools[0]["description"] == blob
        assert client.connected is True

        process.stdout.feed_eof()
        await _drain_reader(client)


class TestConnectFailureCleanup:
    """A handshake that never completes must not leak the child process."""

    @pytest.mark.asyncio
    async def test_failed_connect_reaps_subprocess(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(client_mod, "_REQUEST_TIMEOUT", 0.3)

        spawned: list[Any] = []
        real_exec = asyncio.create_subprocess_exec

        async def spy_exec(*args: Any, **kwargs: Any) -> Any:
            process = await real_exec(*args, **kwargs)
            spawned.append((process, kwargs))
            return process

        monkeypatch.setattr(asyncio, "create_subprocess_exec", spy_exec)

        # `sleep` never answers on stdout, so initialize can only time out.
        client = McpClient(_stdio_config("sleep", ["30"]))

        with pytest.raises(RuntimeError, match="timed out"):
            await client.connect()

        assert spawned, "no subprocess was created"
        process, kwargs = spawned[0]
        assert kwargs.get("limit") == _MAX_RESPONSE_BYTES

        await asyncio.wait_for(process.wait(), timeout=5.0)
        assert process.returncode is not None
        assert client._process is None
        assert client.connected is False

    @pytest.mark.asyncio
    async def test_failed_connect_leaves_no_reader_task(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(client_mod, "_REQUEST_TIMEOUT", 0.3)

        created: list[asyncio.Task[Any]] = []
        real_create_task = asyncio.create_task

        def spy_create_task(coro: Any, **kwargs: Any) -> asyncio.Task[Any]:
            task = real_create_task(coro, **kwargs)
            created.append(task)
            return task

        monkeypatch.setattr(asyncio, "create_task", spy_create_task)

        client = McpClient(_stdio_config("sleep", ["30"]))

        with pytest.raises(RuntimeError, match="timed out"):
            await client.connect()

        assert created, "no reader task was created"
        assert all(task.done() for task in created)
        assert client._reader_task is None
