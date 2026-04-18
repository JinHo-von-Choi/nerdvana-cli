"""External project integration tests — Phase H.

Marked with @pytest.mark.lsp_integration: these tests spawn a real Python
subprocess (a tiny echo server) to verify the full stdio MCP round-trip.

작성자: 최진호
작성일: 2026-04-18
"""

from __future__ import annotations

import asyncio
import json
import sys
import textwrap
from pathlib import Path
from typing import Any

import pytest

from nerdvana_cli.core.external_projects import ExternalProject
from nerdvana_cli.server.external_worker import ExternalSession, ExternalWorker


# ---------------------------------------------------------------------------
# Minimal echo server (runs as a subprocess)
# ---------------------------------------------------------------------------

_ECHO_SERVER_SRC = textwrap.dedent("""\
    import sys, json

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        req = json.loads(line)
        method = req.get("method", "")
        req_id = req.get("id")

        if method == "initialize":
            resp = {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {},
                    "serverInfo": {"name": "echo-server", "version": "0.1.0"},
                },
            }
            print(json.dumps(resp), flush=True)

        elif method == "notifications/initialized":
            pass  # notification — no response

        elif method == "tools/call":
            question = req.get("params", {}).get("arguments", {}).get("question", "")
            resp = {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [{"type": "text", "text": f"echo: {question}"}],
                },
            }
            print(json.dumps(resp), flush=True)

        elif method == "shutdown":
            resp = {"jsonrpc": "2.0", "id": req_id, "result": {}}
            print(json.dumps(resp), flush=True)

        elif method == "exit":
            sys.exit(0)
""")


class _EchoWorker(ExternalWorker):
    """ExternalWorker subclass that spawns the echo server instead of nerdvana_cli."""

    def __init__(self, echo_script: Path, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._echo_script = echo_script

    async def spawn(self, project: ExternalProject) -> ExternalSession:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, str(self._echo_script),
            stdin  = asyncio.subprocess.PIPE,
            stdout = asyncio.subprocess.PIPE,
            stderr = asyncio.subprocess.PIPE,
        )
        session = ExternalSession(project=project, process=proc)
        async with self._lock:
            self._active.append(session)
        return session


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def echo_script(tmp_path: Path) -> Path:
    script = tmp_path / "echo_server.py"
    script.write_text(_ECHO_SERVER_SRC, encoding="utf-8")
    return script


@pytest.fixture()
def project(tmp_path: Path) -> ExternalProject:
    return ExternalProject(name="echo", path=str(tmp_path), languages=["python"])


# ---------------------------------------------------------------------------
# Integration tests
# ---------------------------------------------------------------------------

@pytest.mark.lsp_integration
@pytest.mark.asyncio
async def test_echo_query_roundtrip(
    echo_script: Path,
    project:     ExternalProject,
) -> None:
    """Full spawn → MCP initialize → tools/call → shutdown round-trip."""
    worker = _EchoWorker(
        echo_script    = echo_script,
        max_concurrent = 3,
        query_timeout  = 10.0,
        shutdown_timeout = 1.0,
        kill_timeout   = 1.0,
    )
    answer = await worker.send_query(project, "hello world")
    assert answer == "echo: hello world"
    # Subprocess must have been shut down.
    assert project not in [s.project for s in worker.list_active()]


@pytest.mark.lsp_integration
@pytest.mark.asyncio
async def test_echo_query_multiple_questions(
    echo_script: Path,
    project:     ExternalProject,
) -> None:
    """Multiple sequential queries each use their own subprocess."""
    worker = _EchoWorker(
        echo_script    = echo_script,
        max_concurrent = 3,
        query_timeout  = 10.0,
        shutdown_timeout = 1.0,
        kill_timeout   = 1.0,
    )
    for question in ("first", "second", "third"):
        answer = await worker.send_query(project, question)
        assert answer == f"echo: {question}"
    assert len(worker.list_active()) == 0


@pytest.mark.lsp_integration
@pytest.mark.asyncio
async def test_echo_subprocess_exits_after_query(
    echo_script: Path,
    project:     ExternalProject,
) -> None:
    """The subprocess PID is gone from active list immediately after send_query returns."""
    worker = _EchoWorker(
        echo_script    = echo_script,
        max_concurrent = 3,
        query_timeout  = 10.0,
        shutdown_timeout = 1.0,
        kill_timeout   = 1.0,
    )
    _ = await worker.send_query(project, "check lifecycle")
    assert worker.list_active() == []
