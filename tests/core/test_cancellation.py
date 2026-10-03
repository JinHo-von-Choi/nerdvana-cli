"""Aborting a run stops what it is doing now: streams, shell commands, MCP calls and web requests.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

import asyncio
import os
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from unittest.mock import patch

import httpx
import pytest

from nerdvana_cli.core.config.settings import NerdvanaSettings
from nerdvana_cli.core.delegation.subagent import run_subagent
from nerdvana_cli.core.loop import cancellation
from nerdvana_cli.core.loop.agent_loop import AgentLoop
from nerdvana_cli.core.loop.cancellation import race_abort, until_interrupted
from nerdvana_cli.core.loop.subagent_config import SubagentConfig
from nerdvana_cli.core.state.session import SessionStorage
from nerdvana_cli.core.tool import ToolContext, ToolRegistry
from nerdvana_cli.mcp.client import McpClient
from nerdvana_cli.mcp.config import McpServerConfig
from nerdvana_cli.mcp.tools import McpToolAdapter
from nerdvana_cli.providers.base import ProviderEvent
from nerdvana_cli.tools.bash_tool import BashArgs, BashTool
from nerdvana_cli.tools.web_tools import WebFetchArgs, WebFetchTool


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


async def _gone(pid: int, within: float = 2.0) -> bool:
    deadline = time.monotonic() + within
    while time.monotonic() < deadline:
        if not _alive(pid):
            return True
        await asyncio.sleep(0.02)
    return not _alive(pid)


async def _pid_from(path: Path) -> int:
    for _ in range(200):
        if path.exists() and path.read_text().strip():
            return int(path.read_text().strip())
        await asyncio.sleep(0.02)
    raise AssertionError("the command never wrote its pid")


# ---------------------------------------------------------------------------
# race_abort and until_interrupted
# ---------------------------------------------------------------------------


async def test_setting_the_event_cancels_work_that_is_waiting() -> None:
    abort    = asyncio.Event()
    cleaned  = asyncio.Event()

    async def work() -> str:
        try:
            await asyncio.sleep(30)
        finally:
            cleaned.set()
        return "late"

    asyncio.get_running_loop().call_later(0.05, abort.set)
    started = time.monotonic()
    raced   = await race_abort(work(), abort)
    assert raced.aborted and raced.value is None
    assert cleaned.is_set()
    assert time.monotonic() - started < 1.0


async def test_work_that_finishes_first_returns_its_value() -> None:
    async def work() -> str:
        return "done"

    raced = await race_abort(work(), asyncio.Event())
    assert not raced.aborted and raced.value == "done"


async def test_an_exception_of_the_work_reaches_the_caller() -> None:
    async def work() -> str:
        raise ValueError("broken")

    with pytest.raises(ValueError, match="broken"):
        await race_abort(work(), asyncio.Event())


async def test_patience_lets_short_work_finish_before_it_is_cancelled() -> None:
    abort = asyncio.Event()
    abort.set()

    async def short() -> str:
        await asyncio.sleep(0.1)
        return "finished"

    async def long() -> str:
        await asyncio.sleep(30)
        return "never"

    assert (await race_abort(short(), abort, patience=1.0)).value == "finished"
    assert (await race_abort(long(), abort, patience=0.1)).aborted


async def test_cancelling_the_caller_cancels_the_work() -> None:
    cleaned = asyncio.Event()

    async def work() -> None:
        try:
            await asyncio.sleep(30)
        finally:
            cleaned.set()

    caller = asyncio.create_task(race_abort(work(), asyncio.Event()))
    await asyncio.sleep(0.05)
    caller.cancel()
    with pytest.raises(asyncio.CancelledError):
        await caller
    assert cleaned.is_set()


async def test_an_interrupted_stream_stops_and_its_source_is_closed() -> None:
    interrupt = asyncio.Event()
    closed    = asyncio.Event()

    async def source() -> AsyncIterator[int]:
        try:
            yield 1
            yield 2
            await asyncio.sleep(30)
            yield 3
        finally:
            closed.set()

    seen: list[int] = []
    async for item in until_interrupted(source(), interrupt):
        seen.append(item)
        if item == 2:
            asyncio.get_running_loop().call_later(0.05, interrupt.set)
    assert seen == [1, 2]
    assert closed.is_set()


async def test_a_stream_nobody_interrupts_passes_through_whole() -> None:
    async def source() -> AsyncIterator[int]:
        for item in range(5):
            yield item
            await asyncio.sleep(0)

    assert [item async for item in until_interrupted(source(), asyncio.Event())] == [0, 1, 2, 3, 4]


async def test_an_error_of_the_stream_is_raised_after_the_items_before_it() -> None:
    async def source() -> AsyncIterator[int]:
        yield 1
        raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "bad")

    seen: list[int] = []
    with pytest.raises(UnicodeDecodeError):
        async for item in until_interrupted(source(), asyncio.Event()):
            seen.append(item)
    assert seen == [1]


# ---------------------------------------------------------------------------
# Bash
# ---------------------------------------------------------------------------


async def test_cancelling_a_bash_call_ends_the_command_and_what_it_started(tmp_path: Path) -> None:
    pidfile = tmp_path / "pid"
    call    = asyncio.create_task(BashTool().call(BashArgs(command=f"sleep 60 & echo $! > {pidfile}; wait", timeout=120), ToolContext(cwd=str(tmp_path))))
    pid     = await _pid_from(pidfile)
    started = time.monotonic()
    call.cancel()
    with pytest.raises(asyncio.CancelledError):
        await call
    assert time.monotonic() - started < cancellation.CANCEL_GRACE_SECONDS
    assert await _gone(pid)


async def test_a_command_that_ignores_sigterm_is_killed_after_the_grace_period(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cancellation, "CANCEL_GRACE_SECONDS", 0.5)
    pidfile = tmp_path / "pid"
    call    = asyncio.create_task(BashTool().call(
        BashArgs(command=f"trap '' TERM; sleep 60 & echo $! > {pidfile}; wait", timeout=120), ToolContext(cwd=str(tmp_path)),
    ))
    pid     = await _pid_from(pidfile)
    started = time.monotonic()
    call.cancel()
    with pytest.raises(asyncio.CancelledError):
        await call
    elapsed = time.monotonic() - started
    assert 0.4 <= elapsed < 2.5
    assert await _gone(pid)


async def test_a_timed_out_command_leaves_nothing_running(tmp_path: Path) -> None:
    pidfile = tmp_path / "pid"
    result  = await BashTool().call(BashArgs(command=f"sleep 60 & echo $! > {pidfile}; wait", timeout=1), ToolContext(cwd=str(tmp_path)))
    assert result.is_error and "timed out" in result.content
    assert await _gone(await _pid_from(pidfile))


# ---------------------------------------------------------------------------
# MCP and web tools
# ---------------------------------------------------------------------------


async def test_cancelling_an_mcp_call_returns_at_once() -> None:
    client = McpClient(McpServerConfig(name="slow", command="echo"))
    entered, cleaned = asyncio.Event(), asyncio.Event()

    async def slow_call(name: str, arguments: Any = None) -> dict[str, Any]:
        entered.set()
        try:
            await asyncio.sleep(30)
        finally:
            cleaned.set()
        return {}

    with patch.object(client, "call_tool", slow_call):
        adapter = McpToolAdapter("slow", {"name": "wait"}, client)
        call    = asyncio.create_task(adapter.call({}, ToolContext(cwd="."), None))
        await entered.wait()
        call.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(call, 1.0)
    assert cleaned.is_set()


async def test_cancelling_a_web_request_returns_at_once(monkeypatch: pytest.MonkeyPatch) -> None:
    entered, cleaned = asyncio.Event(), asyncio.Event()

    async def slow_get(self: httpx.AsyncClient, url: str, **_: Any) -> httpx.Response:
        entered.set()
        try:
            await asyncio.sleep(30)
        finally:
            cleaned.set()
        raise AssertionError("the request was not cancelled")

    monkeypatch.setattr(httpx.AsyncClient, "get", slow_get)
    monkeypatch.setattr("nerdvana_cli.tools.web_tools._check_url", lambda url: None)
    call = asyncio.create_task(WebFetchTool().call(WebFetchArgs(url="http://203.0.113.9/"), ToolContext(cwd=".")))
    await entered.wait()
    call.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(call, 1.0)
    assert cleaned.is_set()


# ---------------------------------------------------------------------------
# The run: a sub-agent and the main loop
# ---------------------------------------------------------------------------


class _Hanging:
    """A provider whose response never arrives; records that the request was cancelled."""

    def __init__(self) -> None:
        self.started   = asyncio.Event()
        self.cancelled = False

    async def stream(self, system_prompt: str, messages: Any, tools: Any) -> AsyncIterator[ProviderEvent]:
        self.started.set()
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        yield ProviderEvent(type="done", stop_reason="end_turn")


class _RunsBash:
    """A provider that asks for one shell command and then has nothing more to say."""

    def __init__(self, command: str) -> None:
        self.command = command

    async def stream(self, system_prompt: str, messages: Any, tools: Any) -> AsyncIterator[ProviderEvent]:
        if any(m["role"] == "tool" for m in messages):
            yield ProviderEvent(type="done", stop_reason="end_turn")
            return
        yield ProviderEvent(type="tool_use_complete", tool_use_id="b1", tool_name="Bash", tool_input_complete={"command": self.command, "timeout": 120})
        yield ProviderEvent(type="done", stop_reason="tool_use")


def _config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: Any) -> SubagentConfig:
    monkeypatch.setenv("NERDVANA_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setattr(AgentLoop, "create_provider_from_settings", lambda self: provider)
    monkeypatch.setattr(AgentLoop, "build_system_prompt", lambda self: "system")
    settings     = NerdvanaSettings()
    settings.cwd = str(tmp_path)
    settings.permissions.always_allow = ["Bash"]
    registry = ToolRegistry()
    registry.register(BashTool())
    return SubagentConfig(agent_id="agent_cancel01", name="general-purpose", prompt="go", settings=settings, registry=registry)


async def test_aborting_a_sub_agent_cancels_its_provider_request(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    provider = _Hanging()
    abort    = asyncio.Event()
    config   = _config(tmp_path, monkeypatch, provider)
    run      = asyncio.create_task(run_subagent(config, abort))
    await provider.started.wait()
    started = time.monotonic()
    abort.set()
    output, tokens = await asyncio.wait_for(run, cancellation.CANCEL_GRACE_SECONDS)
    assert output.endswith("[aborted]") and tokens == 0
    assert provider.cancelled
    assert time.monotonic() - started < 1.0


async def test_aborting_a_sub_agent_ends_the_command_it_is_running(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pidfile = tmp_path / "pid"
    abort   = asyncio.Event()
    config  = _config(tmp_path, monkeypatch, _RunsBash(f"sleep 60 & echo $! > {pidfile}; wait"))
    run     = asyncio.create_task(run_subagent(config, abort))
    pid     = await _pid_from(pidfile)
    abort.set()
    output, _ = await asyncio.wait_for(run, cancellation.CANCEL_GRACE_SECONDS)
    assert output.endswith("[aborted]")
    assert await _gone(pid)


async def test_cancelling_the_main_run_ends_the_command_it_is_running(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pidfile = tmp_path / "pid"
    config  = _config(tmp_path, monkeypatch, _RunsBash(f"sleep 60 & echo $! > {pidfile}; wait"))
    loop    = AgentLoop(settings=config.settings, registry=config.registry, session=SessionStorage(session_id="main", storage_dir=str(tmp_path / "s")))

    async def drive() -> None:
        async for _ in loop.run("go"):
            pass

    run = asyncio.create_task(drive())
    pid = await _pid_from(pidfile)
    run.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(run, cancellation.CANCEL_GRACE_SECONDS)
    assert await _gone(pid)


async def test_cancelling_the_main_run_cancels_the_provider_request(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    provider = _Hanging()
    config   = _config(tmp_path, monkeypatch, provider)
    loop     = AgentLoop(settings=config.settings, registry=config.registry, session=SessionStorage(session_id="main2", storage_dir=str(tmp_path / "s")))

    async def drive() -> None:
        async for _ in loop.run("go"):
            pass

    run = asyncio.create_task(drive())
    await provider.started.wait()
    run.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(run, 2.0)
    assert provider.cancelled
