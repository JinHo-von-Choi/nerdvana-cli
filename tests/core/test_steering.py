"""Steering a running agent: queue mode waits for the next step, interrupt mode stops the step in progress.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from nerdvana_cli.commands.steer_command import USAGE, handle_steer
from nerdvana_cli.core.agent_loop import AgentLoop
from nerdvana_cli.core.config.settings import NerdvanaSettings
from nerdvana_cli.core.input_queue import INTERRUPTED_NOTE, InputQueue
from nerdvana_cli.core.state.session import SessionStorage
from nerdvana_cli.core.tool import BaseTool, ToolRegistry
from nerdvana_cli.providers.base import ProviderEvent
from nerdvana_cli.types import ToolResult
from nerdvana_cli.ui.app import NerdvanaApp

# ---------------------------------------------------------------------------
# The queue
# ---------------------------------------------------------------------------


def test_queue_mode_holds_text_without_interrupting() -> None:
    queue = InputQueue("queue")
    assert queue.put("later") is False
    assert queue.pending() and not queue.interrupt.is_set()


def test_interrupt_mode_interrupts_and_taking_the_text_clears_the_request() -> None:
    queue = InputQueue("interrupt")
    assert queue.put("now") is True
    assert queue.interrupt.is_set()
    assert queue.take() == ["now"]
    assert not queue.interrupt.is_set()


def test_a_steer_interrupts_in_queue_mode_and_a_plain_text_can_opt_out_in_interrupt_mode() -> None:
    queue = InputQueue("queue")
    assert queue.put("steer", interrupt=True) is True and queue.interrupt.is_set()
    queue.take()
    other = InputQueue("interrupt")
    assert other.put("wait", interrupt=False) is False and not other.interrupt.is_set()


def test_blank_text_neither_queues_nor_interrupts() -> None:
    queue = InputQueue("interrupt")
    assert queue.put("  \n") is False
    assert not queue.pending() and not queue.interrupt.is_set()


def test_the_mode_is_a_setting_that_defaults_to_queue() -> None:
    assert NerdvanaSettings().session.steer_mode == "queue"
    with pytest.raises(ValueError):
        NerdvanaSettings(session={"steer_mode": "sideways"})  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# The loop
# ---------------------------------------------------------------------------


class _Slow(BaseTool[Any]):
    """A tool that runs until it is cancelled or its time is up."""

    name             = "Slow"
    description_text = "slow"

    def __init__(self, seconds: float) -> None:
        self.seconds   = seconds
        self.running   = asyncio.Event()
        self.cancelled = False

    async def call(self, args: Any, context: Any, can_use_tool: Any = None, on_progress: Any = None) -> ToolResult:
        self.running.set()
        try:
            await asyncio.sleep(self.seconds)
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        return ToolResult(tool_use_id="", content="slow output")


class _Script:
    """Provider responses in order; a response may end with a stall that only a cancellation breaks."""

    def __init__(self, responses: list[list[ProviderEvent]], stall_first: bool = False) -> None:
        self.responses   = list(responses)
        self.stall_first = stall_first
        self.payloads:   list[list[dict[str, Any]]] = []
        self.streaming   = asyncio.Event()
        self.cancelled   = False

    async def stream(self, system_prompt: str, messages: Any, tools: Any) -> AsyncIterator[ProviderEvent]:
        self.payloads.append([dict(m) for m in messages])
        events = self.responses.pop(0) if self.responses else [ProviderEvent(type="done", stop_reason="end_turn")]
        stall  = self.stall_first and len(self.payloads) == 1
        for event in events:
            yield event
            if stall and event.type == "content_delta":
                self.streaming.set()
                try:
                    await asyncio.sleep(60)
                except asyncio.CancelledError:
                    self.cancelled = True
                    raise


def _end(text: str = "ok") -> list[ProviderEvent]:
    return [ProviderEvent(type="content_delta", content=text), ProviderEvent(type="done", stop_reason="end_turn")]


def _tool_turn(name: str = "Slow") -> list[ProviderEvent]:
    return [
        ProviderEvent(type="tool_use_complete", tool_use_id="c1", tool_name=name, tool_input_complete={}),
        ProviderEvent(type="done", stop_reason="tool_use"),
    ]


def _loop(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, provider: _Script, tools: list[BaseTool[Any]] | None = None, mode: str = "queue") -> AgentLoop:
    monkeypatch.setenv("NERDVANA_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setattr(AgentLoop, "create_provider_from_settings", lambda self: provider)
    monkeypatch.setattr(AgentLoop, "build_system_prompt", lambda self: "system")
    registry = ToolRegistry()
    for tool in tools or []:
        registry.register(tool)
    settings = NerdvanaSettings()
    settings.cwd = str(tmp_path)
    settings.session.steer_mode = mode  # type: ignore[assignment]
    settings.permissions.always_allow = ["Slow"]
    return AgentLoop(settings=settings, registry=registry, session=SessionStorage(session_id="steer", storage_dir=str(tmp_path / "sessions")))


async def _drive(loop: AgentLoop, prompt: str = "go") -> list[str]:
    return [chunk async for chunk in loop.run(prompt)]


async def test_interrupt_mode_stops_the_response_in_progress_and_the_next_step_gets_the_text(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    provider = _Script([_end("a long answer"), _end("redirected")], stall_first=True)
    loop     = _loop(monkeypatch, tmp_path, provider, mode="interrupt")
    run      = asyncio.create_task(_drive(loop))
    await provider.streaming.wait()
    assert loop.queue_input("use pytest instead") is True
    chunks = await asyncio.wait_for(run, 3.0)

    assert provider.cancelled
    assert len(provider.payloads) == 2
    assert provider.payloads[1][-1] == {"role": "user", "content": "use pytest instead"}
    assert "a long answer" not in [m.content for m in loop.state.messages]
    assert chunks[-1] == "redirected"
    assert not loop.has_queued_input()


async def test_queue_mode_lets_the_response_finish_and_delivers_the_text_after_it(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    class _Finishes(_Script):
        async def stream(self, system_prompt: str, messages: Any, tools: Any) -> AsyncIterator[ProviderEvent]:
            if not self.payloads:
                loop_ref[0].queue_input("one more thing")
            async for event in super().stream(system_prompt, messages, tools):
                yield event

    loop_ref: list[AgentLoop] = []
    provider = _Finishes([_end("first answer"), _end("second answer")])
    loop     = _loop(monkeypatch, tmp_path, provider, mode="queue")
    loop_ref.append(loop)
    chunks = await _drive(loop)

    assert "first answer" in chunks
    assert not provider.cancelled
    assert provider.payloads[1][-1] == {"role": "user", "content": "one more thing"}


async def test_a_steer_interrupts_even_in_queue_mode(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    provider = _Script([_end("slow answer"), _end("redirected")], stall_first=True)
    loop     = _loop(monkeypatch, tmp_path, provider, mode="queue")
    run      = asyncio.create_task(_drive(loop))
    await provider.streaming.wait()
    assert loop.queue_input("stop", interrupt=True) is True
    await asyncio.wait_for(run, 3.0)
    assert provider.cancelled and provider.payloads[1][-1]["content"] == "stop"


async def test_a_long_tool_is_cancelled_and_its_call_is_answered_so_the_history_stays_paired(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    tool     = _Slow(60)
    provider = _Script([_tool_turn(), _end("redirected")])
    loop     = _loop(monkeypatch, tmp_path, provider, [tool], mode="interrupt")
    loop.input_queue.patience = 0.1
    run      = asyncio.create_task(_drive(loop))
    await tool.running.wait()
    loop.queue_input("never mind, stop")
    await asyncio.wait_for(run, 3.0)

    second = provider.payloads[1]
    assert tool.cancelled
    assert [m["role"] for m in second][-3:] == ["assistant", "tool", "user"]
    assert second[-2]["content"] == INTERRUPTED_NOTE and second[-2]["is_error"] is True
    assert second[-1]["content"] == "never mind, stop"
    recorded = [e for e in loop.session.replay() if e["type"] == "tool_result"]
    assert recorded and recorded[-1]["tool_use_id"] == "c1"


async def test_a_tool_that_ends_within_the_patience_window_keeps_its_result(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    tool     = _Slow(0.2)
    provider = _Script([_tool_turn(), _end("done")])
    loop     = _loop(monkeypatch, tmp_path, provider, [tool], mode="interrupt")
    loop.input_queue.patience = 2.0
    run      = asyncio.create_task(_drive(loop))
    await tool.running.wait()
    loop.queue_input("also check the tests")
    await asyncio.wait_for(run, 3.0)

    second = provider.payloads[1]
    assert not tool.cancelled
    assert second[-2]["content"] == "slow output"
    assert second[-1]["content"] == "also check the tests"


async def test_a_run_nobody_types_into_is_unaffected(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    tool     = _Slow(0.05)
    provider = _Script([_tool_turn(), _end("finished")])
    loop     = _loop(monkeypatch, tmp_path, provider, [tool], mode="interrupt")
    chunks   = await _drive(loop)

    assert chunks[-1] == "finished" and not tool.cancelled
    assert len(provider.payloads) == 2
    assert not loop.input_queue.interrupt.is_set()


# ---------------------------------------------------------------------------
# /steer
# ---------------------------------------------------------------------------


class _FakeLoop:
    def __init__(self) -> None:
        self.calls: list[tuple[str, bool | None]] = []

    def queue_input(self, text: str, interrupt: bool | None = None) -> bool:
        self.calls.append((text, interrupt))
        return bool(interrupt)


class _FakeApp:
    _start_prompt = NerdvanaApp._start_prompt

    def __init__(self, generating: bool, loop: Any = None) -> None:
        self._is_generating = generating
        self._agent_loop    = loop
        self.messages: list[str] = []
        self.sent:     list[str] = []

    def _add_chat_message(self, markup: str, **_: Any) -> None:
        self.messages.append(markup)

    def _generate_response(self, prompt: str) -> None:
        self.sent.append(prompt)


async def test_steer_while_the_agent_works_interrupts() -> None:
    loop = _FakeLoop()
    app  = _FakeApp(True, loop)
    await handle_steer(app, "  go left  ")  # type: ignore[arg-type]
    assert loop.calls == [("go left", True)]
    assert "interrupting" in app.messages[0]


async def test_steer_while_idle_is_an_ordinary_prompt() -> None:
    loop = _FakeLoop()
    app  = _FakeApp(False, loop)
    await handle_steer(app, "go left")  # type: ignore[arg-type]
    assert loop.calls == [] and app.sent == ["go left"]


async def test_steer_without_text_shows_the_usage() -> None:
    app = _FakeApp(True, _FakeLoop())
    await handle_steer(app, "  ")  # type: ignore[arg-type]
    assert USAGE in app.messages[0] and app._agent_loop.calls == []  # type: ignore[attr-defined]


def test_typed_text_says_whether_it_interrupts_or_waits() -> None:
    class _Loop:
        def __init__(self, interrupts: bool) -> None:
            self.interrupts = interrupts

        def queue_input(self, text: str) -> bool:
            return self.interrupts

    waiting = _FakeApp(True, _Loop(False))
    waiting._start_prompt("fix it", "fix it")
    assert "queued" in waiting.messages[0]
    stopping = _FakeApp(True, _Loop(True))
    stopping._start_prompt("fix it", "fix it")
    assert "interrupting" in stopping.messages[0]
