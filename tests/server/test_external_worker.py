"""ExternalWorker subprocess orchestrator tests — Phase H.

Uses AsyncMock to fake subprocess stdio without real process spawning.

작성자: 최진호
작성일: 2026-04-18
"""

from __future__ import annotations

import asyncio
import json
import signal
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nerdvana_cli.core.external_projects import ExternalProject
from nerdvana_cli.server.external_worker import ExternalSession, ExternalWorker


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_project(tmp_path: Path, name: str = "testproj") -> ExternalProject:
    return ExternalProject(name=name, path=str(tmp_path), languages=["python"])


def _jsonl(*payloads: dict[str, Any]) -> list[bytes]:
    """Return a list of newline-terminated JSON bytes."""
    return [(json.dumps(p) + "\n").encode() for p in payloads]


def _mock_process(
    returncode: int | None = None,
    stdout_lines: list[bytes] | None = None,
) -> MagicMock:
    """Build a fake asyncio.subprocess.Process."""
    proc           = MagicMock()
    proc.pid       = 12345
    proc.returncode = returncode
    proc.stdin     = AsyncMock()
    proc.stdin.is_closing.return_value = False

    if stdout_lines:
        proc.stdout = AsyncMock()
        proc.stdout.readline = AsyncMock(side_effect=stdout_lines + [b""])
    else:
        proc.stdout = AsyncMock()
        proc.stdout.readline = AsyncMock(return_value=b"")

    proc.wait      = AsyncMock(return_value=0)
    proc.send_signal = MagicMock()
    proc.kill      = MagicMock()
    return proc


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def project(tmp_path: Path) -> ExternalProject:
    return _make_project(tmp_path)


@pytest.fixture()
def worker() -> ExternalWorker:
    return ExternalWorker(
        max_concurrent   = 3,
        query_timeout    = 5.0,
        shutdown_timeout = 0.1,
        kill_timeout     = 0.1,
    )


# ---------------------------------------------------------------------------
# Spawn tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_spawn_creates_session(worker: ExternalWorker, project: ExternalProject) -> None:
    mock_proc = _mock_process(returncode=None)
    with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=mock_proc)):
        session = await worker.spawn(project)
    assert session.project is project
    assert session.pid == 12345
    assert session in worker.list_active()


@pytest.mark.asyncio
async def test_spawn_passes_project_path_in_cmd(
    worker: ExternalWorker,
    project: ExternalProject,
) -> None:
    mock_proc = _mock_process(returncode=None)
    with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=mock_proc)) as mock_exec:
        await worker.spawn(project)
    cmd = mock_exec.call_args[0]
    assert "--project" in cmd
    idx = list(cmd).index("--project")
    assert cmd[idx + 1] == project.path


# ---------------------------------------------------------------------------
# Shutdown tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_shutdown_removes_from_active(worker: ExternalWorker, project: ExternalProject) -> None:
    mock_proc = _mock_process(returncode=None)
    with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=mock_proc)):
        session = await worker.spawn(project)

    assert session in worker.list_active()
    mock_proc.wait = AsyncMock(return_value=0)
    await worker.shutdown(session)
    assert session not in worker.list_active()


@pytest.mark.asyncio
async def test_shutdown_already_exited(worker: ExternalWorker, project: ExternalProject) -> None:
    """Shutdown on an already-terminated process should not raise."""
    mock_proc = _mock_process(returncode=0)  # already done
    session = ExternalSession(project=project, process=mock_proc)
    await worker.shutdown(session)  # must not raise


@pytest.mark.asyncio
async def test_shutdown_sigterm_fallback(worker: ExternalWorker, project: ExternalProject) -> None:
    """If clean MCP shutdown times out, SIGTERM is sent."""
    mock_proc          = _mock_process(returncode=None)
    call_count         = 0

    async def _wait_side_effect() -> int:
        nonlocal call_count
        call_count += 1
        if call_count <= 1:
            raise asyncio.TimeoutError
        return 0

    mock_proc.wait = AsyncMock(side_effect=_wait_side_effect)
    session = ExternalSession(project=project, process=mock_proc)
    async with worker._lock:
        worker._active.append(session)

    await worker.shutdown(session)
    mock_proc.send_signal.assert_called_with(signal.SIGTERM)


# ---------------------------------------------------------------------------
# Concurrency cap tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_max_concurrent_raises_when_full(tmp_path: Path) -> None:
    """A 4th concurrent request raises RuntimeError immediately."""
    worker = ExternalWorker(max_concurrent=3, query_timeout=1.0)

    # Drain the semaphore manually.
    for _ in range(3):
        await worker._semaphore.acquire()

    project = _make_project(tmp_path)
    with pytest.raises(RuntimeError, match="queue full"):
        await worker.send_query(project, "hello")

    # Release acquired slots.
    for _ in range(3):
        worker._semaphore.release()


@pytest.mark.asyncio
async def test_max_concurrent_default_is_three() -> None:
    worker = ExternalWorker()
    assert worker._semaphore._value == 3  # noqa: SLF001


# ---------------------------------------------------------------------------
# Timeout test
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_send_query_timeout(worker: ExternalWorker, project: ExternalProject) -> None:
    """A slow subprocess triggers TimeoutError and still shuts down cleanly."""
    mock_proc = _mock_process(returncode=None)

    # Make readline hang forever.
    async def _hang() -> bytes:
        await asyncio.sleep(9999)
        return b""

    mock_proc.stdout.readline = AsyncMock(side_effect=_hang)
    mock_proc.wait = AsyncMock(return_value=0)

    fast_worker = ExternalWorker(
        max_concurrent   = 3,
        query_timeout    = 0.05,
        shutdown_timeout = 0.05,
        kill_timeout     = 0.05,
    )
    with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=mock_proc)):
        with pytest.raises(asyncio.TimeoutError):
            await fast_worker.send_query(project, "slow query")

    # Session must have been removed from active list after timeout.
    assert project not in [s.project for s in fast_worker.list_active()]


# ---------------------------------------------------------------------------
# Environment injection test
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_env_injection(worker: ExternalWorker, project: ExternalProject, monkeypatch: Any) -> None:
    """ANTHROPIC_API_KEY from parent env is forwarded to the subprocess."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-from-parent")
    mock_proc = _mock_process(returncode=None)

    captured_env: dict[str, str] = {}

    async def _fake_exec(*args: Any, **kwargs: Any) -> MagicMock:
        captured_env.update(kwargs.get("env", {}))
        return mock_proc

    with patch("asyncio.create_subprocess_exec", new=_fake_exec):
        session = await worker.spawn(project)

    assert captured_env.get("ANTHROPIC_API_KEY") == "test-key-from-parent"
    # Clean up
    await worker.shutdown(session)
