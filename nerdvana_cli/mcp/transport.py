"""Transports for the MCP client that cap what a server may send.

The SDK ships a stdio transport and an HTTP transport, and the SDK ``Client`` drives both. Neither limits
how much one server message may weigh, so a hostile or broken server could make the process buffer
without end. The two transports here are what the client plugs into the SDK ``Client`` instead:

- ``stdio_transport`` starts the server process and reads its stdout line by line. A line over the limit
  ends the connection, because the stream position is lost once part of a line is dropped.
- ``capped_http_client`` returns an HTTP client that refuses a response body over the limit while it is
  still streaming in. Event streams are exempt, as they last as long as the server keeps them open; the
  size of one event is capped by the SDK transport (``max_sse_event_size``).

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

import logging
import sys
from collections.abc import AsyncGenerator, AsyncIterator, Callable, Mapping, Sequence
from contextlib import asynccontextmanager, suppress

import anyio
import anyio.abc
import httpx2
import mcp_types as types
from mcp.client._transport import TransportStreams
from mcp.os.posix.utilities import terminate_posix_process_tree
from mcp.shared.message import SessionMessage

logger = logging.getLogger(__name__)

MAX_MESSAGE_BYTES = 10 * 1024 * 1024  # 10 MB

_READ_CHUNK_BYTES     = 64 * 1024
_STDERR_LOG_CHARS     = 500
_EXIT_GRACE_SECONDS   = 2.0
_KILL_REAP_SECONDS    = 2.0
_EXIT_POLL_SECONDS    = 0.01
_HTTP_CONNECT_SECONDS = 30.0
_HTTP_READ_SECONDS    = 300.0


class ResponseTooLargeError(RuntimeError):
    """A server response is larger than the limit."""


@asynccontextmanager
async def stdio_transport(
    argv:      Sequence[str],
    env:       Mapping[str, str],
    *,
    name:      str,
    on_closed: Callable[[], None],
    limit:     int = MAX_MESSAGE_BYTES,
) -> AsyncGenerator[TransportStreams, None]:
    """Start *argv* and speak newline-delimited JSON-RPC with it over stdin and stdout.

    *on_closed* runs when the server's stdout ends or a line passes *limit*, so the owner can mark the
    connection dead before the next request. The process is stopped, with its whole process group, when
    the context exits.

    Raises:
        FileNotFoundError: The command does not exist.
    """
    process = await anyio.open_process(list(argv), env=dict(env), start_new_session=True)
    assert process.stdin and process.stdout and process.stderr
    read_writer, read_stream   = anyio.create_memory_object_stream[SessionMessage | Exception](0)
    write_stream, write_reader = anyio.create_memory_object_stream[SessionMessage](0)

    async def pump_stdout() -> None:
        async with read_writer:
            try:
                await _read_lines(process.stdout, read_writer, limit, name)  # type: ignore[arg-type]
            finally:
                on_closed()
        await _discard(process.stdout)  # type: ignore[arg-type]

    async def pump_stdin() -> None:
        try:
            async with write_reader:
                async for message in write_reader:
                    data = message.message.model_dump_json(by_alias=True, exclude_unset=True) + "\n"
                    await process.stdin.send(data.encode("utf-8"))  # type: ignore[union-attr]
        except (anyio.ClosedResourceError, anyio.BrokenResourceError, OSError):
            await read_writer.aclose()

    async def log_stderr() -> None:
        with suppress(anyio.EndOfStream, anyio.ClosedResourceError, anyio.BrokenResourceError, OSError):
            while True:
                chunk = await process.stderr.receive(_READ_CHUNK_BYTES)  # type: ignore[union-attr]
                logger.debug("MCP server '%s' stderr: %s", name, chunk.decode("utf-8", "replace")[:_STDERR_LOG_CHARS])

    async with anyio.create_task_group() as tg:
        for pump in (pump_stdout, pump_stdin, log_stderr):
            tg.start_soon(pump)
        try:
            yield read_stream, write_stream
        finally:
            with anyio.CancelScope(shield=True):
                write_stream.close()
                read_stream.close()
                await _stop_process(process)
            tg.cancel_scope.cancel()


async def _read_lines(
    stdout: anyio.abc.ByteReceiveStream,
    sink:   anyio.abc.ObjectSendStream[SessionMessage | Exception],
    limit:  int,
    name:   str,
) -> None:
    """Forward each stdout line to *sink* as a parsed message; stop at end of stream or on an oversized line."""
    buffer = bytearray()
    try:
        while True:
            buffer.extend(await stdout.receive(_READ_CHUNK_BYTES))
            while (end := buffer.find(b"\n")) >= 0:
                line = bytes(buffer[:end])
                del buffer[: end + 1]
                if len(line) > limit:
                    logger.error("MCP server '%s' sent a line over %d bytes; closing the connection", name, limit)
                    return
                if line.strip():
                    await sink.send(_parse_line(line, name))
            if len(buffer) > limit:
                logger.error("MCP server '%s' sent a line over %d bytes; closing the connection", name, limit)
                return
    except (anyio.EndOfStream, anyio.ClosedResourceError, anyio.BrokenResourceError, OSError):
        return


async def _discard(stdout: anyio.abc.ByteReceiveStream) -> None:
    """Read and drop what the server still writes, so that one blocked on a full pipe can exit."""
    with suppress(anyio.EndOfStream, anyio.ClosedResourceError, anyio.BrokenResourceError, OSError):
        while True:
            await stdout.receive(_READ_CHUNK_BYTES)


def _parse_line(line: bytes, name: str) -> SessionMessage | Exception:
    try:
        return SessionMessage(types.jsonrpc_message_adapter.validate_json(line, by_name=False))
    except ValueError as exc:
        logger.warning("Invalid JSON-RPC message from MCP server '%s': %s", name, line[:200])
        return exc


async def _stop_process(process: anyio.abc.Process) -> None:
    """Close stdin, give the server a moment to exit, then end its process group."""
    assert process.stdin and process.stdout and process.stderr
    with suppress(OSError, anyio.BrokenResourceError, anyio.ClosedResourceError):
        await process.stdin.aclose()
    if not await _exited(process, _EXIT_GRACE_SECONDS):
        if sys.platform == "win32":  # pragma: no cover
            process.kill()
        else:
            await terminate_posix_process_tree(process)
        await _exited(process, _KILL_REAP_SECONDS)
    for pipe in (process.stdout, process.stderr):
        with suppress(OSError, anyio.BrokenResourceError, anyio.ClosedResourceError):
            await pipe.aclose()


async def _exited(process: anyio.abc.Process, timeout: float) -> bool:
    """Whether the process ended within *timeout* seconds, by polling the exit code."""
    deadline = anyio.current_time() + timeout
    while process.returncode is None:
        if anyio.current_time() >= deadline:
            return False
        await anyio.sleep(_EXIT_POLL_SECONDS)
    return True


class _CappedStream(httpx2.AsyncByteStream):
    """A response body that raises once it has delivered more than *limit* bytes."""

    def __init__(self, inner: httpx2.AsyncByteStream, limit: int) -> None:
        self._inner = inner
        self._limit = limit

    async def __aiter__(self) -> AsyncIterator[bytes]:
        total = 0
        async for chunk in self._inner:
            total += len(chunk)
            if total > self._limit:
                raise ResponseTooLargeError(f"MCP response exceeds {self._limit} bytes")
            yield chunk

    async def aclose(self) -> None:
        await self._inner.aclose()


class _CappedTransport(httpx2.AsyncBaseTransport):
    """Wraps a transport so that no response body, except an event stream, passes the limit."""

    def __init__(self, inner: httpx2.AsyncBaseTransport, limit: int) -> None:
        self._inner = inner
        self._limit = limit

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        response = await self._inner.handle_async_request(request)
        if "text/event-stream" in response.headers.get("content-type", ""):
            return response
        declared = response.headers.get("content-length", "")
        if declared.isdigit() and int(declared) > self._limit:
            await response.aclose()
            raise ResponseTooLargeError(f"MCP response exceeds {self._limit} bytes (got {declared})")
        response.stream = _CappedStream(response.stream, self._limit)  # type: ignore[arg-type]
        return response

    async def aclose(self) -> None:
        await self._inner.aclose()


def capped_http_client(headers: Mapping[str, str], limit: int = MAX_MESSAGE_BYTES) -> httpx2.AsyncClient:
    """An HTTP client for one MCP server: TLS verified, *headers* on every request, bodies capped at *limit*."""
    transport = _CappedTransport(httpx2.AsyncHTTPTransport(verify=True), limit)
    timeout   = httpx2.Timeout(_HTTP_CONNECT_SECONDS, read=_HTTP_READ_SECONDS)
    return httpx2.AsyncClient(transport=transport, headers=dict(headers), timeout=timeout)

