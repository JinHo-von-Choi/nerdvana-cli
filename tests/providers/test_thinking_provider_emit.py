"""Unit tests — thinking_delta emission from Anthropic and OpenAI providers."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nerdvana_cli.providers.anthropic_provider import AnthropicProvider
from nerdvana_cli.providers.base import (
    ProviderConfig,
    ProviderEvent,
    ProviderName,
)
from nerdvana_cli.providers.openai_provider import OpenAIProvider

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_anthropic_config() -> ProviderConfig:
    return ProviderConfig(
        provider=ProviderName.ANTHROPIC,
        api_key="test-key",
        model="claude-sonnet-4-20250514",
    )


def _make_openai_config() -> ProviderConfig:
    return ProviderConfig(
        provider=ProviderName.OPENAI,
        api_key="test-key",
        model="gpt-4.1",
    )


async def _collect(provider: Any, system: str = "", messages: list | None = None) -> list[ProviderEvent]:
    events: list[ProviderEvent] = []
    async for ev in provider.stream(system, messages or [], []):
        events.append(ev)
    return events


# ---------------------------------------------------------------------------
# Anthropic — fake async iterator
# ---------------------------------------------------------------------------

def _make_sdk_event(event_type: str, **kwargs: Any) -> MagicMock:
    ev = MagicMock()
    ev.type = event_type
    for k, v in kwargs.items():
        setattr(ev, k, v)
    return ev


def _make_delta(delta_type: str, **kwargs: Any) -> MagicMock:
    d = MagicMock()
    d.type = delta_type
    for k, v in kwargs.items():
        setattr(d, k, v)
    return d


def _anthropic_stream_events(sequence: list[MagicMock]):
    """Return an async iterator that yields the given events."""

    async def _iter():
        for ev in sequence:
            yield ev

    return _iter()


@pytest.mark.asyncio
async def test_anthropic_thinking_delta_emitted() -> None:
    """thinking_delta event emitted once with correct .thinking text."""
    events_sdk = [
        _make_sdk_event("message_start", message=MagicMock(usage=MagicMock(input_tokens=10))),
        _make_sdk_event(
            "content_block_delta",
            delta=_make_delta("text_delta", text="Hello "),
        ),
        _make_sdk_event(
            "content_block_delta",
            delta=_make_delta("thinking_delta", thinking="I reason here"),
        ),
        _make_sdk_event(
            "content_block_delta",
            delta=_make_delta("text_delta", text="world"),
        ),
        _make_sdk_event(
            "message_delta",
            usage=MagicMock(output_tokens=5),
            delta=MagicMock(stop_reason="end_turn"),
        ),
    ]

    provider = AnthropicProvider(_make_anthropic_config())

    async def _fake_create(**_kwargs: Any):
        return _anthropic_stream_events(events_sdk)

    with patch.object(provider, "_get_client") as mock_client:
        mock_client.return_value.messages.create = AsyncMock(side_effect=_fake_create)
        evs = await _collect(provider)

    thinking_evs = [e for e in evs if e.type == "thinking_delta"]
    content_evs  = [e for e in evs if e.type == "content_delta"]

    assert len(thinking_evs) == 1
    assert thinking_evs[0].thinking == "I reason here"
    assert any(e.content == "Hello " for e in content_evs)
    assert any(e.content == "world"   for e in content_evs)


@pytest.mark.asyncio
async def test_anthropic_no_thinking_when_absent() -> None:
    """No thinking_delta events when the stream contains only text_delta."""
    events_sdk = [
        _make_sdk_event("message_start", message=MagicMock(usage=MagicMock(input_tokens=5))),
        _make_sdk_event(
            "content_block_delta",
            delta=_make_delta("text_delta", text="Just text"),
        ),
        _make_sdk_event(
            "message_delta",
            usage=MagicMock(output_tokens=2),
            delta=MagicMock(stop_reason="end_turn"),
        ),
    ]

    provider = AnthropicProvider(_make_anthropic_config())

    async def _fake_create(**_kwargs: Any):
        return _anthropic_stream_events(events_sdk)

    with patch.object(provider, "_get_client") as mock_client:
        mock_client.return_value.messages.create = AsyncMock(side_effect=_fake_create)
        evs = await _collect(provider)

    assert not any(e.type == "thinking_delta" for e in evs)
    assert any(e.type == "content_delta" for e in evs)


# ---------------------------------------------------------------------------
# OpenAI-compatible — ThinkBlockParser integration
# ---------------------------------------------------------------------------

def _make_chunk(content: str | None, finish_reason: str | None = None) -> MagicMock:
    chunk     = MagicMock()
    choice    = MagicMock()
    choice.delta.content    = content
    choice.delta.tool_calls = None
    choice.finish_reason    = finish_reason
    chunk.choices = [choice]
    chunk.usage   = None
    return chunk


def _make_stream(chunks: list[MagicMock]):
    """Return an async context manager that iterates over chunks."""

    class _FakeStream:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            pass

        def __aiter__(self):
            return self._gen()

        async def _gen(self):
            for c in chunks:
                yield c

    return _FakeStream()


async def _collect_openai(chunks: list[MagicMock]) -> list[ProviderEvent]:
    provider = OpenAIProvider(_make_openai_config())

    async def _fake_create(**_kwargs: Any):
        return _make_stream(chunks)

    with patch.object(provider, "_get_client") as mock_client:
        # Both with and without stream_options path go through the same mock
        mock_client.return_value.chat.completions.create = AsyncMock(side_effect=_fake_create)
        evs: list[ProviderEvent] = []
        async for ev in provider.stream("", [], []):
            evs.append(ev)
    return evs


@pytest.mark.asyncio
async def test_openai_think_tags_in_single_chunk() -> None:
    """content before/after <think> tag goes to content_delta; interior goes to thinking_delta."""
    chunks = [
        _make_chunk("abc<think>reason</think>def"),
        _make_chunk(None, finish_reason="stop"),
    ]
    evs = await _collect_openai(chunks)

    content_text  = "".join(e.content  for e in evs if e.type == "content_delta")
    thinking_text = "".join(e.thinking for e in evs if e.type == "thinking_delta")

    assert content_text  == "abcdef"
    assert thinking_text == "reason"


@pytest.mark.asyncio
async def test_openai_think_tags_split_across_chunks() -> None:
    """Parser buffers partial tags correctly across chunk boundaries."""
    chunks = [
        _make_chunk("abc<thi"),
        _make_chunk("nk>reason</think>def"),
        _make_chunk(None, finish_reason="stop"),
    ]
    evs = await _collect_openai(chunks)

    content_text  = "".join(e.content  for e in evs if e.type == "content_delta")
    thinking_text = "".join(e.thinking for e in evs if e.type == "thinking_delta")

    assert content_text  == "abcdef"
    assert thinking_text == "reason"


@pytest.mark.asyncio
async def test_openai_no_think_tags() -> None:
    """Plain content without think tags produces only content_delta events."""
    chunks = [
        _make_chunk("hello world"),
        _make_chunk(None, finish_reason="stop"),
    ]
    evs = await _collect_openai(chunks)

    assert not any(e.type == "thinking_delta" for e in evs)
    content_text = "".join(e.content for e in evs if e.type == "content_delta")
    assert content_text == "hello world"
