"""MCP stdio client wrapper for @nerdvana/parism."""

from __future__ import annotations

import asyncio
import json
import shutil
from typing import Any


class ParismClient:
    """Manages a persistent MCP stdio connection to parism."""

    def __init__(self, cwd: str = "."):
        self._process: asyncio.subprocess.Process | None = None
        self._connected = False
        self._cwd = cwd
        self._request_id = 0
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._reader_task: asyncio.Task[None] | None = None

    @property
    def is_connected(self) -> bool:
        return self._connected

    @classmethod
    def is_available(cls) -> bool:
        """Check if npx is available on PATH."""
        return shutil.which("npx") is not None

    async def connect(self) -> None:
        """Start parism subprocess and initialize MCP handshake."""
        if self._connected:
            return

        self._process = await asyncio.create_subprocess_exec(
            "npx", "-y", "@nerdvana/parism",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=self._cwd,
        )

        self._reader_task = asyncio.create_task(self._read_loop())

        # MCP initialize handshake
        await self._send_request("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "nerdvana-cli", "version": "0.1.1"},
        })

        await self._send_notification("notifications/initialized", {})
        self._connected = True

    async def disconnect(self) -> None:
        """Gracefully shut down the parism process."""
        if not self._connected:
            return
        self._connected = False
        if self._process and self._process.stdin:
            self._process.stdin.close()
        if self._reader_task:
            self._reader_task.cancel()
        if self._process:
            try:
                await asyncio.wait_for(self._process.wait(), timeout=5)
            except TimeoutError:
                self._process.kill()

    async def run(self, cmd: str, args: list[str] | None = None,
                  cwd: str | None = None, output_format: str = "json") -> dict[str, Any]:
        """Execute a command via parism 'run' tool."""
        if not self._connected:
            raise RuntimeError("Parism client not connected")
        return await self._call_tool("run", {
            "cmd": cmd,
            "args": args or [],
            "cwd": cwd or self._cwd,
            "format": output_format,
        })

    async def run_paged(self, cmd: str, args: list[str] | None = None,
                        cwd: str | None = None, page: int = 0,
                        page_size: int = 100) -> dict[str, Any]:
        """Execute a command with paged output."""
        if not self._connected:
            raise RuntimeError("Parism client not connected")
        return await self._call_tool("run_paged", {
            "cmd": cmd,
            "args": args or [],
            "cwd": cwd or self._cwd,
            "page": page,
            "page_size": page_size,
        })

    async def _call_tool(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Send MCP tools/call request and await response."""
        result = await self._send_request("tools/call", {
            "name": tool_name,
            "arguments": arguments,
        })
        # MCP tool result has content array; parism returns JSON in first text block
        content = result.get("content", [])
        if content and content[0].get("type") == "text":
            return dict(json.loads(content[0]["text"]))
        return result

    async def _send_request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        """Send JSON-RPC request and await response."""
        self._request_id += 1
        req_id = self._request_id
        msg = {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params}

        future: asyncio.Future[dict[str, Any]] = asyncio.get_event_loop().create_future()
        self._pending[req_id] = future

        raw = json.dumps(msg) + "\n"
        assert self._process is not None
        assert self._process.stdin is not None
        self._process.stdin.write(raw.encode())
        await self._process.stdin.drain()

        return await asyncio.wait_for(future, timeout=30)

    async def _send_notification(self, method: str, params: dict[str, Any]) -> None:
        """Send JSON-RPC notification (no response expected)."""
        msg = {"jsonrpc": "2.0", "method": method, "params": params}
        raw = json.dumps(msg) + "\n"
        assert self._process is not None
        assert self._process.stdin is not None
        self._process.stdin.write(raw.encode())
        await self._process.stdin.drain()

    async def _read_loop(self) -> None:
        """Read JSON-RPC responses from parism stdout."""
        while True:
            try:
                assert self._process is not None
                assert self._process.stdout is not None
                line = await self._process.stdout.readline()
                if not line:
                    break
                data = json.loads(line.decode())
                req_id = data.get("id")
                if req_id and req_id in self._pending:
                    if "error" in data:
                        self._pending[req_id].set_exception(
                            RuntimeError(data["error"].get("message", "Unknown error"))
                        )
                    else:
                        self._pending[req_id].set_result(data.get("result", {}))
                    del self._pending[req_id]
            except (json.JSONDecodeError, asyncio.CancelledError):
                break
            except Exception:
                continue
