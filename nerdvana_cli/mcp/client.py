"""MCP client for one server over stdio or streamable HTTP, built on the SDK ``Client``.

The SDK client negotiates the protocol revision itself: it asks a server to ``server/discover`` first
(the 2026-07-28 revision, stateless, no ``initialize``) and falls back to the ``initialize`` handshake of
the 2025 revisions. It also honours the ``ttlMs`` and ``cacheScope`` hints of list results with an
in-memory cache, and drives ``input_required`` results (see ``input_requests.py``).

What this module adds on top:

- the transports of ``transport.py``, which cap one server message at 10 MB;
- one background task per connection that owns the SDK client, because the SDK scopes its resources to
  the task that entered it, while this class is connected and closed from different tasks;
- a request timeout of 30 seconds, and ``RuntimeError`` for every failure, as callers expect;
- results as plain dicts in the shape of the wire format, which ``tools.py`` and ``manager.py`` read;
- confinement of stdio server processes (``sandbox.py``).

Not exposed to the application by the SDK: tasks (the SDK dropped its experimental client), and the
client-side halves of sampling, roots and logging, which this client leaves at the SDK defaults (refused).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from typing import Any
from urllib.parse import urlparse

import mcp_types as types
from mcp import Client, MCPError
from mcp.client.extension import advertise
from mcp.client.streamable_http import streamable_http_client
from pydantic import BaseModel, ValidationError

from nerdvana_cli import __version__
from nerdvana_cli.mcp.config import McpServerConfig
from nerdvana_cli.mcp.input_requests import answer_elicitation
from nerdvana_cli.mcp.sandbox import NOT_LOCAL, plan_server_launch
from nerdvana_cli.mcp.transport import MAX_MESSAGE_BYTES, capped_http_client, stdio_transport

logger = logging.getLogger(__name__)

_REQUEST_TIMEOUT    = 30.0
_MAX_RESPONSE_BYTES = MAX_MESSAGE_BYTES
_STOP_TIMEOUT       = 10.0
_MAX_LIST_PAGES     = 100
_LOCAL_HOSTNAMES    = frozenset({"localhost", "127.0.0.1", "::1"})
_CLIENT_NAME        = "nerdvana-cli"
_SKILLS_EXTENSION   = "io.modelcontextprotocol/skills"


class _WireResult(types.Result):
    """Any result, kept as it came: the fields of a method the SDK has no model for."""

    model_config = {**types.Result.model_config, "extra": "allow"}


class _WireRequest(types.Request[dict[str, Any], str]):
    """A request for a method the SDK has no model for (the extensions)."""


def _dump(model: BaseModel) -> dict[str, Any]:
    return model.model_dump(by_alias=True, mode="json", exclude_none=True)


class McpClient:
    """Manages a single MCP server connection via stdio or HTTP transport."""

    def __init__(self, config: McpServerConfig) -> None:
        self._config = config
        self._sdk: Client | None = None
        self._runner: asyncio.Task[None] | None = None
        self._stop: asyncio.Event | None = None
        self._connected = False
        self._confinement = NOT_LOCAL

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def confinement(self) -> str:
        """How the server process is confined: ``confined``, ``unconfined`` (with the reason) or ``not applicable``."""
        return self._confinement

    @property
    def extensions(self) -> dict[str, Any]:
        """The extensions the server declared, by identifier; empty until connected."""
        if self._sdk is None:
            return {}
        return dict(self._sdk.server_capabilities.extensions or {})

    async def connect(self) -> dict[str, Any]:
        """Connect to the MCP server and negotiate the protocol revision.

        Returns:
            The negotiated ``protocolVersion``, the server's ``capabilities`` and, when it gave one, its ``serverInfo``.

        Raises:
            RuntimeError: When the server cannot be started, does not answer in time, or refuses the handshake.
        """
        if self._config.transport in ("http", "sse"):
            self._warn_if_insecure_transport(self._config.url)
        self._stop = asyncio.Event()
        ready: asyncio.Future[Client] = asyncio.get_running_loop().create_future()
        self._runner = asyncio.create_task(self._run(ready))
        try:
            sdk = await asyncio.wait_for(ready, timeout=_REQUEST_TIMEOUT)
        except TimeoutError as exc:
            await self._abort()
            raise RuntimeError(f"MCP request 'initialize' timed out after {_REQUEST_TIMEOUT}s") from exc
        except BaseException:
            await self._abort()
            raise
        result: dict[str, Any] = {
            "protocolVersion": sdk.protocol_version,
            "capabilities":    _dump(sdk.server_capabilities),
        }
        if sdk.server_info is not None:
            result["serverInfo"] = _dump(sdk.server_info)
        return result

    async def _run(self, ready: asyncio.Future[Client]) -> None:
        """Own the SDK client for the life of the connection; runs as the one task that enters and leaves it."""
        try:
            async with self._build_client() as sdk:
                self._sdk       = sdk
                self._connected = True
                if not ready.done():
                    ready.set_result(sdk)
                assert self._stop is not None
                await self._stop.wait()
        except asyncio.CancelledError:
            raise
        except BaseException as exc:
            if not ready.done():
                ready.set_exception(self._startup_error(exc))
            else:
                logger.debug("MCP connection to '%s' ended with %r", self._config.name, exc)
        finally:
            self._connected = False
            self._sdk       = None

    def _startup_error(self, exc: BaseException) -> RuntimeError:
        while isinstance(exc, BaseExceptionGroup) and len(exc.exceptions) == 1:
            exc = exc.exceptions[0]
        if isinstance(exc, RuntimeError):
            return exc
        if isinstance(exc, MCPError):
            return RuntimeError(f"MCP error {exc.code}: {exc.error.message}")
        if isinstance(exc, FileNotFoundError):
            return RuntimeError(f"MCP server command not found: {self._config.command}")
        return RuntimeError(f"MCP handshake with '{self._config.name}' failed: {exc}")

    def _build_client(self) -> Client:
        """The SDK client for this server's transport."""
        if self._config.transport in ("http", "sse"):
            http      = capped_http_client(self._config.headers, _MAX_RESPONSE_BYTES)
            transport = streamable_http_client(self._config.url, http_client=http, max_sse_event_size=_MAX_RESPONSE_BYTES)
        else:
            transport = self._stdio_transport()
        return Client(
            transport,
            read_timeout_seconds = _REQUEST_TIMEOUT,
            client_info          = types.Implementation(name=_CLIENT_NAME, version=__version__),
            elicitation_callback = answer_elicitation,
            extensions           = [advertise(_SKILLS_EXTENSION)],
        )

    def _stdio_transport(self) -> Any:
        """The transport that starts the server process, confined as its ``sandbox`` setting asks."""
        launch            = plan_server_launch(self._config, os.getcwd())
        self._confinement = launch.status
        if launch.status.startswith("unconfined") and self._config.sandbox != "off":
            logger.warning("MCP server '%s' runs %s", self._config.name, launch.status)
        return stdio_transport(
            launch.argv,
            {**os.environ, **self._config.env},
            name      = self._config.name,
            on_closed = self._mark_closed,
            limit     = _MAX_RESPONSE_BYTES,
        )

    def _mark_closed(self) -> None:
        self._connected = False

    def _warn_if_insecure_transport(self, url: str) -> None:
        """Log a WARNING when the MCP server uses non-TLS HTTP to a non-local host.

        Local development servers on localhost / 127.0.0.1 / ::1 are exempt
        because plaintext loopback traffic is the common case for dev tooling.
        """
        parsed   = urlparse(url)
        scheme   = (parsed.scheme or "").lower()
        hostname = (parsed.hostname or "").lower()

        if scheme == "http" and hostname not in _LOCAL_HOSTNAMES:
            logger.warning(
                "MCP server '%s' uses insecure http:// transport: %s",
                self._config.name,
                url,
            )

    async def disconnect(self) -> None:
        """Gracefully shut down the MCP server connection."""
        self._connected = False
        runner, stop    = self._runner, self._stop
        self._runner    = None
        if runner is None:
            return
        if stop is not None:
            stop.set()
        try:
            await asyncio.wait_for(asyncio.shield(runner), timeout=_STOP_TIMEOUT)
        except TimeoutError:
            await self._abort(runner)

    async def _abort(self, runner: asyncio.Task[None] | None = None) -> None:
        """Cancel the connection task, which ends the transport and, with it, the server process."""
        runner, self._runner = runner or self._runner, None
        self._connected      = False
        if runner is not None:
            runner.cancel()
            with contextlib.suppress(BaseException):
                await runner

    def _ensure_connected(self) -> Client:
        if not self._connected or self._sdk is None:
            raise RuntimeError(
                f"MCP client not connected to '{self._config.name}'. "
                "Call connect() first."
            )
        return self._sdk

    def _failure(self, exc: Exception, method: str) -> RuntimeError:
        """The ``RuntimeError`` that reports *exc*, a failure of the request *method*."""
        if isinstance(exc, RuntimeError):
            return exc
        if isinstance(exc, MCPError):
            if exc.code == types.CONNECTION_CLOSED:
                self._connected = False
            if exc.code == types.REQUEST_TIMEOUT:
                return RuntimeError(f"MCP request '{method}' timed out after {_REQUEST_TIMEOUT}s")
            return RuntimeError(f"MCP error {exc.code}: {exc.error.message}")
        if isinstance(exc, TimeoutError):
            return RuntimeError(f"MCP request '{method}' timed out after {_REQUEST_TIMEOUT}s")
        if isinstance(exc, ValidationError):
            return RuntimeError(f"MCP server '{self._config.name}' sent an invalid '{method}' result: {exc.error_count()} errors")
        return RuntimeError(f"MCP request '{method}' failed: {exc}")

    async def list_tools(self) -> list[dict[str, Any]]:
        """Request the list of tools from the MCP server, following pagination.

        The first page is served from the SDK cache while the server's ``ttlMs`` has not run out.

        Returns:
            List of tool definitions.

        Raises:
            RuntimeError: If not connected.
        """
        sdk = self._ensure_connected()
        tools: list[dict[str, Any]] = []
        cursor: str | None          = None
        try:
            for _ in range(_MAX_LIST_PAGES):
                page   = await sdk.list_tools(cursor=cursor)
                tools += [_dump(tool) for tool in page.tools]
                cursor = page.next_cursor
                if not cursor:
                    break
        except Exception as exc:
            raise self._failure(exc, "tools/list") from exc
        return tools

    async def call_tool(
        self, name: str, arguments: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Call a tool on the MCP server.

        A result that asks for more input is answered through the question channel bound with
        ``input_requests.bind_ask_user`` around the call, or refused when there is none.

        Args:
            name: Tool name.
            arguments: Tool arguments.

        Returns:
            Tool call result dict (``content``, ``isError`` and, when the server sent it, ``structuredContent``).

        Raises:
            RuntimeError: If not connected or call fails.
        """
        sdk = self._ensure_connected()
        try:
            result = await sdk.call_tool(name, arguments or {})
        except Exception as exc:
            raise self._failure(exc, "tools/call") from exc
        return _dump(result)

    async def list_resources(self) -> list[dict[str, Any]]:
        """Request the list of resources from the MCP server.

        Returns:
            List of resource definitions.

        Raises:
            RuntimeError: If not connected.
        """
        sdk = self._ensure_connected()
        try:
            result = await sdk.list_resources()
        except Exception as exc:
            raise self._failure(exc, "resources/list") from exc
        return [_dump(resource) for resource in result.resources]

    async def read_resource(self, uri: str) -> list[dict[str, Any]]:
        """Read one resource and return its ``contents`` (each with ``text`` or a base64 ``blob``).

        Raises:
            RuntimeError: If not connected or the read fails.
        """
        sdk = self._ensure_connected()
        try:
            result = await sdk.read_resource(uri)
        except Exception as exc:
            raise self._failure(exc, "resources/read") from exc
        return [_dump(content) for content in result.contents]

    async def request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        """Send a request of an extension method, which the SDK has no typed call for, and return its result.

        Raises:
            RuntimeError: If not connected or the server answers with an error.
        """
        sdk = self._ensure_connected()
        try:
            result = await sdk.session.send_request(_WireRequest(method=method, params=params), _WireResult)
        except Exception as exc:
            raise self._failure(exc, method) from exc
        return _dump(result)

