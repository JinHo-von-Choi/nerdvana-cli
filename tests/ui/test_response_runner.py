"""Tests for the streaming response runner.

Covers the chunk routing of the agent-loop stream, the completion path, and the
teardown that runs when a stream is cancelled or fails: the elapsed-time timer
must stop, the in-progress widget markers must be cleared, and text already
streamed must not be thrown away.

작성자: 최진호
작성일: 2026-09-11
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import pytest

from nerdvana_cli.core.agent_loop import (
    COMPACT_STATUS_PREFIX,
    CONTEXT_USAGE_PREFIX,
    TOOL_DONE_PREFIX,
    TOOL_STATUS_PREFIX,
)
from nerdvana_cli.ui.response_runner import run_response_stream

# ---------------------------------------------------------------------------
# Doubles
# ---------------------------------------------------------------------------


class _Widget:
    """Records the calls the runner makes on a streaming/tool-status widget."""

    def __init__(self) -> None:
        self.classes:  set[str]  = set()
        self.updates:  list[Any] = []
        self.contents: list[str] = []
        self.thinking: list[str] = []
        self.resets:   int       = 0

    def add_class(self, name: str) -> None:
        self.classes.add(name)

    def remove_class(self, name: str) -> None:
        self.classes.discard(name)

    def update(self, value: Any = "") -> None:
        self.updates.append(value)

    def update_content(self, text: str) -> None:
        self.contents.append(text)

    def update_thinking(self, text: str) -> None:
        self.thinking.append(text)

    def reset(self) -> None:
        self.resets += 1


class _StatusBar:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def update_status(self, **kwargs: Any) -> None:
        self.calls.append(kwargs)


class _ChatFrame:
    def __init__(self) -> None:
        self.scrolls = 0

    def scroll_end(self, animate: bool = True) -> None:
        self.scrolls += 1


class _Usage:
    def __init__(self, tokens_in: int = 11, tokens_out: int = 22) -> None:
        self.input_tokens  = tokens_in
        self.output_tokens = tokens_out


class _State:
    def __init__(self) -> None:
        self.usage = _Usage()


class _Registry:
    def all_tools(self) -> list[str]:
        return ["a", "b", "c"]


class _Loop:
    """Agent loop stand-in yielding a scripted chunk sequence."""

    def __init__(
        self,
        chunks:        list[str],
        raises:        BaseException | None = None,
        last_thinking: str                  = "",
        thinking_emit: str | None           = None,
    ) -> None:
        self.state               = _State()
        self.registry            = _Registry()
        self.last_thinking       = last_thinking
        self._on_thinking_chunk: Any = None
        self._chunks             = chunks
        self._raises             = raises
        self._thinking_emit      = thinking_emit
        self.prompts: list[str]  = []

    async def run(self, prompt: str) -> AsyncIterator[str]:
        self.prompts.append(prompt)
        if self._thinking_emit is not None and self._on_thinking_chunk:
            self._on_thinking_chunk(self._thinking_emit)
        for chunk in self._chunks:
            await asyncio.sleep(0)
            yield chunk
        if self._raises is not None:
            raise self._raises


class _Model:
    model    = "opus"
    provider = "anthropic"


class _Settings:
    model = _Model()


class _App:
    """Minimal stand-in for NerdvanaApp, addressed by widget selector."""

    def __init__(self, loop: _Loop | None) -> None:
        self._agent_loop        = loop
        self.settings           = _Settings()
        self.parism_client: Any = None
        self._is_generating     = False
        self.streaming          = _Widget()
        self.tool_status        = _Widget()
        self.status_bar         = _StatusBar()
        self.chat_frame         = _ChatFrame()
        self.messages: list[tuple[str, dict[str, Any]]] = []
        self.context_usage: list[int]                   = []

    def query_one(self, selector: Any, widget_type: type | None = None) -> Any:
        if not isinstance(selector, str):
            return self.tool_status          # query_one(ToolStatusLine)
        return {
            "#streaming-output": self.streaming,
            "#tool-status":      self.tool_status,
            "#status-bar":       self.status_bar,
            "#chat-frame":       self.chat_frame,
        }[selector]

    def _add_chat_message(self, message: str, **kwargs: Any) -> None:
        self.messages.append((message, kwargs))

    def _update_context_usage(self, pct: int) -> None:
        self.context_usage.append(pct)

    @property
    def texts(self) -> list[str]:
        return [message for message, _ in self.messages]


def _app(chunks: list[str], **kwargs: Any) -> _App:
    return _App(_Loop(chunks, **kwargs))


# ---------------------------------------------------------------------------
# Completion path
# ---------------------------------------------------------------------------


class TestCompletion:
    async def test_streams_text_and_commits_it(self) -> None:
        app = _app(["Hel", "lo"])
        await run_response_stream(app, "hi")

        assert app.streaming.contents == ["Hel", "Hello"]
        assert app.texts[0] == "Hello"
        assert app.messages[0][1]["raw_text"] == "Hello"
        assert app._is_generating is False

    async def test_clears_in_progress_markers(self) -> None:
        app = _app(["done"])
        await run_response_stream(app, "hi")

        assert "active" not in app.streaming.classes
        assert "active" not in app.tool_status.classes
        assert app.streaming.resets == 1

    async def test_reports_elapsed_time_and_usage(self) -> None:
        app = _app(["x"])
        await run_response_stream(app, "hi")

        summary = app.texts[-1]
        assert "11 in / 22 out" in summary
        assert summary.startswith("[dim](")

    async def test_final_status_bar_update_is_not_thinking(self) -> None:
        app = _app(["x"])
        await run_response_stream(app, "hi")

        final = app.status_bar.calls[-1]
        assert final["tokens_in"]  == 11
        assert final["tokens_out"] == 22
        assert final["tools"]      == 3
        assert final["parism"] is False
        assert "thinking" not in final

    async def test_thinking_timer_reports_progress(self) -> None:
        app = _app(["x"])
        await run_response_stream(app, "hi")
        assert any(call.get("thinking") for call in app.status_bar.calls)

    async def test_blank_output_is_not_committed_as_a_message(self) -> None:
        app = _app(["   ", "\n"])
        await run_response_stream(app, "hi")
        assert all(text.startswith("[dim](") for text in app.texts)

    async def test_thinking_chunks_reach_the_streaming_widget(self) -> None:
        app = _app(["x"], thinking_emit="pondering")
        await run_response_stream(app, "hi")
        assert app.streaming.thinking == ["pondering"]

    async def test_prompt_is_forwarded_to_the_loop(self) -> None:
        app = _app(["x"])
        await run_response_stream(app, "explain this")
        assert app._agent_loop is not None
        assert app._agent_loop.prompts == ["explain this"]

    async def test_thinking_buffer_is_attached_to_the_message(self) -> None:
        app = _app(["answer"], last_thinking="chain of thought")
        await run_response_stream(app, "hi")
        assert app.messages[0][1]["thinking"] == "chain of thought"


# ---------------------------------------------------------------------------
# Control-chunk routing
# ---------------------------------------------------------------------------


class TestControlChunks:
    async def test_context_usage_chunk(self) -> None:
        app = _app([f"{CONTEXT_USAGE_PREFIX}73", "text"])
        await run_response_stream(app, "hi")

        assert app.context_usage == [73]
        assert app.streaming.contents == ["text"]

    async def test_tool_status_chunk_marks_active_and_scrolls(self) -> None:
        app = _app([f"{TOOL_STATUS_PREFIX}Read(foo.py)"])
        await run_response_stream(app, "hi")

        assert app.tool_status.updates
        assert app.chat_frame.scrolls >= 1

    async def test_tool_done_success_chunk(self) -> None:
        app = _app([f"{TOOL_DONE_PREFIX}Read(foo.py) ok"])
        await run_response_stream(app, "hi")

        rendered = app.tool_status.updates[-1].plain
        assert "✓" in rendered
        assert "Read(foo.py) ok" in rendered

    async def test_tool_done_error_chunk(self) -> None:
        app = _app([f"{TOOL_DONE_PREFIX}Read(foo.py) [error] boom"])
        await run_response_stream(app, "hi")

        rendered = app.tool_status.updates[-1].plain
        assert "✗" in rendered

    async def test_bracket_in_tool_name_is_escaped_not_dropped(self) -> None:
        app = _app([f"{TOOL_STATUS_PREFIX}Bash(ls [a-z])"])
        await run_response_stream(app, "hi")

        rendered = app.tool_status.updates[-1].plain
        assert "[a-z]" in rendered

    async def test_compact_in_progress_marks_active(self) -> None:
        app = _app([f"{COMPACT_STATUS_PREFIX}start"])
        await run_response_stream(app, "hi")

        assert any("compressing context" in str(u) for u in app.tool_status.updates)

    @pytest.mark.parametrize("signal", ["done", "fallback"])
    async def test_compact_finished_clears_active(self, signal: str) -> None:
        app = _app([f"{COMPACT_STATUS_PREFIX}{signal}"])
        await run_response_stream(app, "hi")

        assert "active" not in app.tool_status.classes


# ---------------------------------------------------------------------------
# Failure and cancellation teardown
# ---------------------------------------------------------------------------


class TestTeardown:
    async def test_error_is_reported_and_state_reset(self) -> None:
        app = _app(["partial"], raises=RuntimeError("provider down"))
        await run_response_stream(app, "hi")

        assert "provider down" in app.texts[-1]
        assert app._is_generating is False
        assert "active" not in app.streaming.classes
        assert "active" not in app.tool_status.classes
        assert app.streaming.resets == 1

    async def test_cancellation_reraises(self) -> None:
        app = _app(["half an answer"], raises=asyncio.CancelledError())
        with pytest.raises(asyncio.CancelledError):
            await run_response_stream(app, "hi")

    async def test_cancellation_keeps_what_was_already_streamed(self) -> None:
        app = _app(["half an answer"], raises=asyncio.CancelledError())
        with pytest.raises(asyncio.CancelledError):
            await run_response_stream(app, "hi")

        assert app.texts == ["half an answer"]
        assert app.messages[0][1]["raw_text"] == "half an answer"

    async def test_cancellation_clears_in_progress_markers(self) -> None:
        app = _app([f"{TOOL_STATUS_PREFIX}Read(x)"], raises=asyncio.CancelledError())
        with pytest.raises(asyncio.CancelledError):
            await run_response_stream(app, "hi")

        assert "active" not in app.tool_status.classes
        assert "active" not in app.streaming.classes
        assert app.streaming.resets == 1
        assert app._is_generating is False

    async def test_cancellation_stops_the_elapsed_timer(self) -> None:
        app = _app(["text"], raises=asyncio.CancelledError())
        with pytest.raises(asyncio.CancelledError):
            await run_response_stream(app, "hi")

        settled = len(app.status_bar.calls)
        await asyncio.sleep(0.6)
        assert len(app.status_bar.calls) == settled

    async def test_cancellation_with_nothing_streamed_commits_nothing(self) -> None:
        app = _app([], raises=asyncio.CancelledError())
        with pytest.raises(asyncio.CancelledError):
            await run_response_stream(app, "hi")

        assert app.messages == []
