"""OpenAI-compatible streaming: each tool call and the final stop are reported once.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from nerdvana_cli.providers.base import ProviderConfig, ProviderEvent
from nerdvana_cli.providers.openai_provider import OpenAIProvider, _stop_reason


def _provider() -> OpenAIProvider:
    return OpenAIProvider(ProviderConfig(provider="openai", model="m", api_key="k", max_tokens=100))


def _call(index: int | None, call_id: str | None = None, name: str | None = None, args: str | None = None) -> Any:
    function = SimpleNamespace(name=name, arguments=args) if (name is not None or args is not None) else None
    return SimpleNamespace(index=index, id=call_id, function=function)


def _chunk(content: str | None = None, calls: list[Any] | None = None, finish: str | None = None) -> Any:
    delta = SimpleNamespace(content=content, tool_calls=calls)
    return SimpleNamespace(choices=[SimpleNamespace(delta=delta, finish_reason=finish)], usage=None)


def _usage_chunk(prompt: int, completion: int) -> Any:
    return SimpleNamespace(choices=[], usage=SimpleNamespace(prompt_tokens=prompt, completion_tokens=completion))


async def _events(monkeypatch: pytest.MonkeyPatch, chunks: list[Any]) -> list[ProviderEvent]:
    provider = _provider()

    async def _stream() -> AsyncIterator[Any]:
        for chunk in chunks:
            yield chunk

    create = AsyncMock(return_value=_stream())
    monkeypatch.setattr(provider, "_get_client", lambda: SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))))
    return [event async for event in provider.stream("sys", [{"role": "user", "content": "go"}], [])]


def _kinds(events: list[ProviderEvent]) -> list[str]:
    return [event.type for event in events]


async def test_a_repeated_final_chunk_does_not_repeat_the_tool_call_or_the_stop(monkeypatch: pytest.MonkeyPatch) -> None:
    events = await _events(monkeypatch, [
        _chunk(calls=[_call(0, "call_a", "Grep", '{"pattern": "x"}')]),
        _chunk(finish="tool_calls"),
        _chunk(finish="tool_calls"),
        _chunk(finish="tool_calls"),
    ])
    assert _kinds(events).count("tool_use_complete") == 1
    assert _kinds(events).count("done") == 1
    assert events[-1].type == "done"
    assert events[-1].stop_reason == "tool_use"


async def test_parallel_calls_that_share_an_index_stay_separate(monkeypatch: pytest.MonkeyPatch) -> None:
    events = await _events(monkeypatch, [
        _chunk(calls=[_call(0, "call_a", "FileRead", '{"path": "a.py"}')]),
        _chunk(calls=[_call(0, "call_b", "FileRead", '{"path": "b.py"}')]),
        _chunk(finish="tool_calls"),
    ])
    calls = [e for e in events if e.type == "tool_use_complete"]
    assert [(c.tool_use_id, c.tool_input_complete) for c in calls] == [
        ("call_a", {"path": "a.py"}),
        ("call_b", {"path": "b.py"}),
    ]


async def test_arguments_streamed_in_pieces_are_joined_and_late_ids_are_kept(monkeypatch: pytest.MonkeyPatch) -> None:
    events = await _events(monkeypatch, [
        _chunk(calls=[_call(0, None, "Grep", '{"pattern":')]),
        _chunk(calls=[_call(0, "call_late", None, ' "x"}')]),
        _chunk(finish="tool_calls"),
    ])
    (call,) = [e for e in events if e.type == "tool_use_complete"]
    assert call.tool_use_id == "call_late"
    assert call.tool_name == "Grep"
    assert call.tool_input_complete == {"pattern": "x"}


async def test_name_repeated_on_every_chunk_is_not_concatenated(monkeypatch: pytest.MonkeyPatch) -> None:
    events = await _events(monkeypatch, [
        _chunk(calls=[_call(0, "call_a", "Grep", '{"pattern":')]),
        _chunk(calls=[_call(0, None, "Grep", ' "x"}')]),
        _chunk(finish="tool_calls"),
    ])
    (call,) = [e for e in events if e.type == "tool_use_complete"]
    assert call.tool_name == "Grep"
    assert call.tool_input_complete == {"pattern": "x"}


async def test_tool_calls_reported_with_finish_reason_stop_still_run(monkeypatch: pytest.MonkeyPatch) -> None:
    events = await _events(monkeypatch, [
        _chunk(calls=[_call(0, "call_a", "Grep", "{}")]),
        _chunk(finish="stop"),
    ])
    assert "tool_use_complete" in _kinds(events)
    assert events[-1].stop_reason == "tool_use"


async def test_real_usage_that_follows_the_finish_chunk_is_used_instead_of_an_estimate(monkeypatch: pytest.MonkeyPatch) -> None:
    events = await _events(monkeypatch, [
        _chunk("hello"),
        _chunk(finish="stop"),
        _usage_chunk(1234, 56),
    ])
    usage = [e.usage for e in events if e.type == "usage"]
    assert usage == [{"input_tokens": 1234, "output_tokens": 56}]
    assert events[-1].type == "done"
    assert events[-1].stop_reason == "end_turn"


async def test_length_finish_maps_to_max_tokens(monkeypatch: pytest.MonkeyPatch) -> None:
    events = await _events(monkeypatch, [_chunk("cut off"), _chunk(finish="length")])
    assert events[-1].stop_reason == "max_tokens"


@pytest.mark.parametrize(
    ("finish", "has_calls", "expected"),
    [
        (None, False, "end_turn"),
        ("stop", False, "end_turn"),
        ("tool_calls", False, "end_turn"),
        ("tool_calls", True, "tool_use"),
        ("stop", True, "tool_use"),
        (None, True, "tool_use"),
        ("length", True, "max_tokens"),
        ("content_filter", False, "content_filter"),
    ],
)
def test_stop_reason_mapping(finish: str | None, has_calls: bool, expected: str) -> None:
    assert _stop_reason(finish, has_calls) == expected


def test_outgoing_history_keeps_ids_as_given() -> None:
    out = _provider()._convert_messages("sys", [
        {"role": "assistant", "content": "", "tool_uses": [{"id": "call_a", "name": "Grep", "input": {}}]},
        {"role": "tool", "content": "ok", "tool_use_id": "call_a"},
    ])
    ids = [call["id"] for message in out for call in message.get("tool_calls", [])]
    assert ids == ["call_a"]
    assert json.dumps(out)


# ---------------------------------------------------------------------------
# Where the usage report can be
# ---------------------------------------------------------------------------


def _chunk_with_usage(finish: str | None, prompt: int, completion: int, cached: int = 0) -> Any:
    delta   = SimpleNamespace(content=None, tool_calls=None)
    details = SimpleNamespace(cached_tokens=cached)
    usage   = SimpleNamespace(prompt_tokens=prompt, completion_tokens=completion, prompt_tokens_details=details)
    return SimpleNamespace(choices=[SimpleNamespace(delta=delta, finish_reason=finish)], usage=usage)


async def test_usage_carried_by_the_finish_chunk_is_reported_not_estimated(monkeypatch: pytest.MonkeyPatch) -> None:
    events = await _events(monkeypatch, [_chunk(content="hello"), _chunk_with_usage("stop", 2000, 7, cached=1900)])
    (usage,) = [e.usage for e in events if e.type == "usage"]
    assert usage == {"input_tokens": 2000, "output_tokens": 7, "cache_read_tokens": 1900}


async def test_a_usage_repeated_on_every_chunk_is_reported_once_with_the_last_figures(monkeypatch: pytest.MonkeyPatch) -> None:
    events = await _events(monkeypatch, [
        _chunk_with_usage(None, 2000, 0),
        _chunk_with_usage(None, 2000, 3),
        _chunk_with_usage("stop", 2000, 9),
    ])
    assert [e.usage for e in events if e.type == "usage"] == [{"input_tokens": 2000, "output_tokens": 9}]


async def test_empty_usage_objects_do_not_hide_the_estimate_when_nothing_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    events = await _events(monkeypatch, [_chunk(content="x" * 40), _chunk_with_usage("stop", 0, 0)])
    (usage,) = [e.usage for e in events if e.type == "usage"]
    assert usage["output_tokens"] == 10 and usage["input_tokens"] > 0


async def test_the_estimate_counts_the_tool_declarations_too(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = _provider()

    async def _stream() -> AsyncIterator[Any]:
        yield _chunk(content="y" * 40)
        yield _chunk(finish="stop")

    create = AsyncMock(return_value=_stream())
    monkeypatch.setattr(provider, "_get_client", lambda: SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))))

    class _Tool:
        name             = "T"
        description_text = "d" * 4000
        input_schema: dict[str, Any] = {"type": "object"}

    events = [e async for e in provider.stream("sys", [{"role": "user", "content": "go"}], [_Tool()])]
    (usage,) = [e.usage for e in events if e.type == "usage"]
    assert usage["input_tokens"] >= 1000


async def test_the_estimate_counts_with_the_counter_the_session_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    config   = ProviderConfig(provider="openai", model="m", api_key="k", max_tokens=100, count_tokens=lambda text: text.count("가"))
    provider = OpenAIProvider(config)

    async def _stream() -> AsyncIterator[Any]:
        yield _chunk(content="가나" * 20)
        yield _chunk(finish="stop")

    create = AsyncMock(return_value=_stream())
    monkeypatch.setattr(provider, "_get_client", lambda: SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))))
    events = [e async for e in provider.stream("sys", [{"role": "user", "content": "가가가"}], [])]
    assert [e.usage for e in events if e.type == "usage"] == [{"input_tokens": 3, "output_tokens": 20}]
