"""Generic MCP client — stdio and HTTP transports via JSON-RPC 2.0."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from typing import Any

from nerdvana_cli.mcp.config import McpServerConfig

logger = logging.getLogger(__name__)

_REQUEST_TIMEOUT = 30.0
_MCP_PROTOCOL_VERSION = "2024-11-05"


class McpClient:
    """Manages a single MCP server connection via stdio or HTTP transport."""

    def __init__(self, config: McpServerConfig) -> None:
        self._config    = config
        self._process: asyncio.subprocess.Process | None = None
        self._reader_task: asyncio.Task | None = None
        self._request_id = 0
        self._pending: dict[int, asyncio.Future[dict]] = {}
        self._connected = False
        self._session_url: str | None = None  # HTTP: server may return session endpoint
        self._http_client: Any = None

    @property
    def connected(self) -> bool:
        return self._connected

    async def connect(self) -> dict[str, Any]:
        """Connect to the MCP server and perform the initialize handshake."""
        if self._config.transport in ("http", "sse"):
            return await self._connect_http()
        return await self._connect_stdio()

    async def _connect_stdio(self) -> dict[str, Any]:
        import os

        env = {**os.environ, **self._config.env}

        try:
            self._process = await asyncio.create_subprocess_exec(
                self._config.command,
                *self._config.args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
        except FileNotFoundError as exc:
            raise RuntimeError(
                f"MCP server command not found: {self._config.command}"
            ) from exc

        self._reader_task = asyncio.create_task(self._read_loop())

        result = await self._send_request("initialize", {
            "protocolVersion": _MCP_PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "nerdvana-cli", "version": "0.1.1"},
        })

        await self._send_notification("notifications/initialized", {})
        self._connected = True
        return result

    async def _connect_http(self) -> dict[str, Any]:
        import httpx

        self._http_client = httpx.AsyncClient(timeout=_REQUEST_TIMEOUT)
        self._session_url = self._config.url

        result = await self._send_request("initialize", {
            "protocolVersion": _MCP_PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "nerdvana-cli", "version": "0.1.1"},
        })

        await self._send_notification("notifications/initialized", {})
        self._connected = True
        return result

    async def disconnect(self) -> None:
        """Gracefully shut down the MCP server connection."""
        if not self._connected:
            return

        self._connected = False

        if self._http_client:
            await self._http_client.aclose()
            self._http_client = None

        if self._process and self._process.stdin:
            self._process.stdin.close()

        if self._reader_task and not self._reader_task.done():
            self._reader_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._reader_task

        if self._process:
            try:
                self._process.terminate()
                await asyncio.wait_for(self._process.wait(), timeout=5.0)
            except (TimeoutError, ProcessLookupError):
                self._process.kill()
            self._process = None

        for future in self._pending.values():
            if not future.done():
                future.set_exception(RuntimeError("Connection closed"))
        self._pending.clear()

    async def list_tools(self) -> list[dict[str, Any]]:
        """Request the list of tools from the MCP server.

        Returns:
            List of tool definitions.

        Raises:
            RuntimeError: If not connected.
        """
        self._ensure_connected()
        result = await self._send_request("tools/list", {})
        return result.get("tools", [])

    async def call_tool(
        self, name: str, arguments: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Call a tool on the MCP server.

        Args:
            name: Tool name.
            arguments: Tool arguments.

        Returns:
            Tool call result dict.

        Raises:
            RuntimeError: If not connected or call fails.
        """
        self._ensure_connected()
        result = await self._send_request("tools/call", {
            "name": name,
            "arguments": arguments or {},
        })
        return result

    async def list_resources(self) -> list[dict[str, Any]]:
        """Request the list of resources from the MCP server.

        Returns:
            List of resource definitions.

        Raises:
            RuntimeError: If not connected.
        """
        self._ensure_connected()
        result = await self._send_request("resources/list", {})
        return result.get("resources", [])

    def _ensure_connected(self) -> None:
        if not self._connected:
            raise RuntimeError(
                f"MCP client not connected to '{self._config.name}'. "
                "Call connect() first."
            )

    async def _send_request(
        self, method: str, params: dict[str, Any]
    ) -> dict[str, Any]:
        """Send a JSON-RPC 2.0 request and wait for the response."""
        self._request_id += 1
        req_id = self._request_id

        message = {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": method,
            "params": params,
        }

        if self._http_client:
            return await self._http_send_request(message)

        loop   = asyncio.get_event_loop()
        future = loop.create_future()
        self._pending[req_id] = future

        self._write_message(message)

        try:
            result = await asyncio.wait_for(future, timeout=_REQUEST_TIMEOUT)
        except TimeoutError as err:
            self._pending.pop(req_id, None)
            raise RuntimeError(
                f"MCP request '{method}' timed out after {_REQUEST_TIMEOUT}s"
            ) from err

        return result

    async def _send_notification(
        self, method: str, params: dict[str, Any]
    ) -> None:
        """Send a JSON-RPC 2.0 notification (no id, no response expected)."""
        message = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
        }
        if self._http_client:
            await self._http_send_notification(message)
        else:
            self._write_message(message)

    def _http_headers(self) -> dict[str, str]:
        """Build headers for MCP Streamable HTTP requests."""
        return {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            **self._config.headers,
        }

    async def _http_send_request(self, message: dict[str, Any]) -> dict[str, Any]:
        """Send JSON-RPC request over HTTP POST (MCP Streamable HTTP)."""
        resp = await self._http_client.post(
            self._session_url,
            json=message,
            headers=self._http_headers(),
        )
        resp.raise_for_status()

        # Track session endpoint
        if "mcp-session-id" in resp.headers:
            session_id = resp.headers["mcp-session-id"]
            # Store for subsequent requests
            self._config.headers["Mcp-Session-Id"] = session_id

        content_type = resp.headers.get("content-type", "")

        if "text/event-stream" in content_type:
            # SSE response — parse last JSON-RPC message from event stream
            return self._parse_sse_response(resp.text)

        data = resp.json()

        if "error" in data:
            error = data["error"]
            raise RuntimeError(
                f"MCP error {error.get('code', '?')}: {error.get('message', 'unknown')}"
            )
        return data.get("result", {})

    def _parse_sse_response(self, text: str) -> dict[str, Any]:
        """Extract last JSON-RPC result from SSE event stream."""
        last_data = ""
        for line in text.split("\n"):
            if line.startswith("data: "):
                last_data = line[6:]
        if not last_data:
            return {}
        try:
            msg = json.loads(last_data)
            if "error" in msg:
                error = msg["error"]
                raise RuntimeError(
                    f"MCP error {error.get('code', '?')}: {error.get('message', 'unknown')}"
                )
            return msg.get("result", {})
        except json.JSONDecodeError:
            return {}

    async def _http_send_notification(self, message: dict[str, Any]) -> None:
        """Send JSON-RPC notification over HTTP POST (fire and forget)."""
        with contextlib.suppress(Exception):
            await self._http_client.post(
                self._session_url,
                json=message,
                headers=self._http_headers(),
            )

    def _write_message(self, message: dict[str, Any]) -> None:
        """Write a JSON-RPC message to the subprocess stdin."""
        if not self._process or not self._process.stdin:
            raise RuntimeError("No subprocess stdin available")

        data = json.dumps(message) + "\n"
        self._process.stdin.write(data.encode("utf-8"))

    async def _read_loop(self) -> None:
        """Continuously read JSON-RPC responses from subprocess stdout."""
        assert self._process and self._process.stdout

        try:
            while True:
                line = await self._process.stdout.readline()
                if not line:
                    break

                line_str = line.decode("utf-8").strip()
                if not line_str:
                    continue

                try:
                    message = json.loads(line_str)
                except json.JSONDecodeError:
                    logger.warning("Invalid JSON from MCP server: %s", line_str[:200])
                    continue

                msg_id = message.get("id")
                if msg_id is not None and msg_id in self._pending:
                    future = self._pending.pop(msg_id)
                    if "error" in message:
                        error = message["error"]
                        future.set_exception(
                            RuntimeError(
                                f"MCP error {error.get('code', '?')}: "
                                f"{error.get('message', 'unknown')}"
                            )
                        )
                    else:
                        future.set_result(message.get("result", {}))
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("MCP read loop error")
