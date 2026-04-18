"""External project subprocess orchestrator — Phase H.

Spawns isolated Python subprocesses to query external project directories
over a stdio MCP channel.  Each subprocess:

- Runs ``python -m nerdvana_cli serve --transport stdio --project <path>
  --mode query --allow-write=false``.
- Receives API tokens via environment variables injected by the parent
  (never stored on the subprocess itself).
- Communicates over stdin/stdout MCP JSON-RPC.
- Is terminated immediately after the query returns.

Concurrency cap: at most 3 active sessions at once (``max_concurrent``).

작성자: 최진호
작성일: 2026-04-18
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import sys
import time
from dataclasses import dataclass, field
from typing import Any

from nerdvana_cli.core.external_projects import ExternalProject

logger = logging.getLogger(__name__)

MAX_CONCURRENT: int = 3
_SHUTDOWN_TIMEOUT: float = 2.0
_KILL_TIMEOUT: float = 2.0
_QUERY_TIMEOUT: float = 30.0


@dataclass
class ExternalSession:
    """Tracks a live subprocess spawned for a single query."""

    project:    ExternalProject
    process:    asyncio.subprocess.Process
    spawned_at: float = field(default_factory=time.monotonic)
    request_id: int   = field(default=0)

    @property
    def pid(self) -> int | None:
        return self.process.pid

    @property
    def is_running(self) -> bool:
        return self.process.returncode is None


class ExternalWorker:
    """Subprocess orchestrator for isolated external-project queries."""

    def __init__(
        self,
        max_concurrent:   int                   = MAX_CONCURRENT,
        query_timeout:    float                 = _QUERY_TIMEOUT,
        shutdown_timeout: float                 = _SHUTDOWN_TIMEOUT,
        kill_timeout:     float                 = _KILL_TIMEOUT,
        extra_env:        dict[str, str] | None = None,
    ) -> None:
        self._semaphore      = asyncio.Semaphore(max_concurrent)
        self._max_concurrent = max_concurrent
        self._query_timeout  = query_timeout
        self._shutdown_timeout = shutdown_timeout
        self._kill_timeout   = kill_timeout
        self._extra_env      = extra_env
        self._active:        list[ExternalSession] = []
        self._lock           = asyncio.Lock()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def send_query(self, project: ExternalProject, question: str) -> str:
        """Spawn a subprocess, send a query, return the response, then shut down.

        Raises RuntimeError when the concurrency cap is exceeded.
        Raises asyncio.TimeoutError when the subprocess is too slow.
        """
        # Non-blocking capacity check before blocking on the semaphore.
        if self._semaphore._value == 0:  # noqa: SLF001
            raise RuntimeError(
                f"External worker queue full: {self._max_concurrent} sessions already active. "
                "Retry after a current query completes."
            )

        async with self._semaphore:
            session = await self.spawn(project)
            try:
                return await asyncio.wait_for(
                    self._do_query(session, question),
                    timeout=self._query_timeout,
                )
            except Exception:
                logger.exception(
                    "Error querying external project %r (pid=%s)",
                    project.name,
                    session.pid,
                )
                raise
            finally:
                await self.shutdown(session)

    async def spawn(self, project: ExternalProject) -> ExternalSession:
        """Spawn a stdio MCP subprocess for the given project.

        Uses asyncio.create_subprocess_exec (not shell=True) to prevent
        shell injection.  API tokens from the parent environment are
        forwarded; the subprocess never acquires tokens independently.
        """
        env = self._build_env()
        cmd = [
            sys.executable, "-m", "nerdvana_cli",
            "serve",
            "--transport", "stdio",
            "--project",   project.path,
            "--mode",      "query",
            "--allow-write=false",
        ]

        logger.debug("Spawning external worker: %s (project=%s)", project.name, project.path)

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin  = asyncio.subprocess.PIPE,
            stdout = asyncio.subprocess.PIPE,
            stderr = asyncio.subprocess.PIPE,
            env    = env,
        )

        session = ExternalSession(project=project, process=proc)
        async with self._lock:
            self._active.append(session)

        logger.debug("Spawned pid=%s for project=%s", proc.pid, project.name)
        return session

    async def shutdown(self, session: ExternalSession) -> None:
        """Gracefully shut down a session.

        Steps:
        1. Send MCP shutdown + exit notifications.
        2. Wait ``shutdown_timeout`` for clean exit.
        3. SIGTERM → wait ``kill_timeout``.
        4. SIGKILL.
        5. Remove from active list.
        """
        async with self._lock:
            if session in self._active:
                self._active.remove(session)

        if not session.is_running:
            return

        try:
            await self._send_mcp_shutdown(session)
        except Exception:
            logger.debug("MCP shutdown notification failed for pid=%s", session.pid)

        try:
            await asyncio.wait_for(session.process.wait(), timeout=self._shutdown_timeout)
            logger.debug("pid=%s exited cleanly", session.pid)
            return
        except TimeoutError:
            pass

        if session.is_running:
            logger.debug("Sending SIGTERM to pid=%s", session.pid)
            try:
                session.process.send_signal(signal.SIGTERM)
            except ProcessLookupError:
                return
            try:
                await asyncio.wait_for(session.process.wait(), timeout=self._kill_timeout)
                return
            except TimeoutError:
                pass

        if session.is_running:
            logger.debug("Sending SIGKILL to pid=%s", session.pid)
            try:
                session.process.kill()
                await session.process.wait()
            except ProcessLookupError:
                pass

    def list_active(self) -> list[ExternalSession]:
        """Return a snapshot of currently active sessions."""
        return list(self._active)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_env(self) -> dict[str, str]:
        """Build the subprocess environment.

        Inherits the parent environment (including ANTHROPIC_API_KEY etc.)
        and applies any extra_env overrides.  Environment variables are
        cleaned up when the subprocess exits (OS-level resource reclaim).
        """
        env = dict(os.environ)
        if self._extra_env:
            env.update(self._extra_env)
        return env

    async def _send_mcp_shutdown(self, session: ExternalSession) -> None:
        """Write MCP shutdown + exit notifications to subprocess stdin."""
        if session.process.stdin is None or session.process.stdin.is_closing():
            return

        shutdown_msg = json.dumps({
            "jsonrpc": "2.0",
            "id":      session.request_id,
            "method":  "shutdown",
            "params":  {},
        }) + "\n"
        exit_notif = json.dumps({
            "jsonrpc": "2.0",
            "method":  "exit",
            "params":  {},
        }) + "\n"

        try:
            session.process.stdin.write(shutdown_msg.encode())
            session.process.stdin.write(exit_notif.encode())
            await session.process.stdin.drain()
            session.process.stdin.close()
        except (BrokenPipeError, ConnectionResetError):
            pass

    async def _do_query(self, session: ExternalSession, question: str) -> str:
        """Run the MCP initialize → query sequence and return the result text."""
        if session.process.stdin is None or session.process.stdout is None:
            raise RuntimeError("Subprocess stdio not available")

        # 1. initialize
        session.request_id += 1
        await self._write_line(session, {
            "jsonrpc": "2.0",
            "id":      session.request_id,
            "method":  "initialize",
            "params":  {
                "protocolVersion": "2025-03-26",
                "capabilities":    {},
                "clientInfo":      {"name": "nerdvana-external-worker", "version": "0.9.0"},
            },
        })
        init_resp = await self._read_line(session)
        if "error" in init_resp:
            raise RuntimeError(f"MCP initialize error: {init_resp['error']}")

        # 2. initialized notification
        await self._write_line(session, {
            "jsonrpc": "2.0",
            "method":  "notifications/initialized",
            "params":  {},
        })

        # 3. tools/call
        session.request_id += 1
        await self._write_line(session, {
            "jsonrpc": "2.0",
            "id":      session.request_id,
            "method":  "tools/call",
            "params":  {
                "name":      "query",
                "arguments": {"question": question},
            },
        })
        call_resp = await self._read_line(session)
        if "error" in call_resp:
            raise RuntimeError(f"Query tool error: {call_resp['error']}")

        result  = call_resp.get("result", {})
        content = result.get("content", [])
        if isinstance(content, list) and content:
            first = content[0]
            if isinstance(first, dict):
                return str(first.get("text", ""))
        return str(result)

    async def _write_line(self, session: ExternalSession, payload: dict[str, Any]) -> None:
        line = (json.dumps(payload, ensure_ascii=False) + "\n").encode()
        assert session.process.stdin is not None
        session.process.stdin.write(line)
        await session.process.stdin.drain()

    async def _read_line(self, session: ExternalSession) -> dict[str, Any]:
        assert session.process.stdout is not None
        raw = await session.process.stdout.readline()
        if not raw:
            raise RuntimeError("Subprocess stdout closed unexpectedly")
        result: dict[str, Any] = json.loads(raw.decode())
        return result
