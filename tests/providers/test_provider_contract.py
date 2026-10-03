"""Contract every provider adapter keeps: one usage report, assembled tool calls, unique ids, classified errors.

Each adapter runs against a fake client that replays a payload written by hand in that SDK's documented
shape. The scenario is the same for all of them; only the wire format differs. Nothing here talks to a
server. docs/providers-compat.md states the same facts in prose.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import anthropic
import httpx
import openai
import pytest
from google.genai import errors as genai_errors
from openai.types.responses import ResponseFunctionToolCall

from nerdvana_cli.providers.anthropic_provider import AnthropicProvider
from nerdvana_cli.providers.base import ProviderConfig, ProviderEvent, ProviderName
from nerdvana_cli.providers.errors import AUTH, CONTEXT_LIMIT, RETRYABLE
from nerdvana_cli.providers.gemini_provider import GeminiProvider
from nerdvana_cli.providers.openai_provider import OpenAIProvider
from nerdvana_cli.providers.openai_responses import OpenAIResponsesProvider, convert_input

TOOL = SimpleNamespace(
    name             = "Grep",
    description_text = "Search files",
    input_schema     = {"type": "object", "properties": {"pattern": {"type": "string"}}, "required": ["pattern"]},
)


@dataclass
class Turn:
    """What a model response contains, independent of the wire format."""

    text:    list[str]                                 = field(default_factory=list)
    calls:   list[tuple[str, str, list[str]]]          = field(default_factory=list)  # (id, name, argument fragments)
    usage:   tuple[int, int, int]                      = (100, 20, 40)                # whole prompt, output, cached part


# ---------------------------------------------------------------------------
# Wire formats
# ---------------------------------------------------------------------------


async def _replay(items: list[Any]) -> AsyncIterator[Any]:
    for item in items:
        yield item


def _chat_chunks(turn: Turn) -> list[Any]:
    prompt, output, cached = turn.usage
    usage  = SimpleNamespace(prompt_tokens=prompt, completion_tokens=output, prompt_tokens_details=SimpleNamespace(cached_tokens=cached))
    chunks: list[Any] = []
    for piece in turn.text:
        chunks.append(SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=piece, tool_calls=None), finish_reason=None)], usage=usage))
    for index, (call_id, name, fragments) in enumerate(turn.calls):
        for position, fragment in enumerate(fragments):
            function = SimpleNamespace(name=name if position == 0 else None, arguments=fragment)
            delta    = SimpleNamespace(content=None, tool_calls=[SimpleNamespace(index=index, id=call_id if position == 0 else None, function=function)])
            chunks.append(SimpleNamespace(choices=[SimpleNamespace(delta=delta, finish_reason=None)], usage=usage))
    finish = "tool_calls" if turn.calls else "stop"
    chunks.append(SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=None, tool_calls=None), finish_reason=finish)], usage=usage))
    chunks.append(SimpleNamespace(choices=[], usage=usage))
    return chunks


def _anthropic_events(turn: Turn) -> list[Any]:
    prompt, output, cached = turn.usage
    start  = SimpleNamespace(input_tokens=prompt - cached, cache_read_input_tokens=cached, cache_creation_input_tokens=0, output_tokens=1)
    events: list[Any] = [SimpleNamespace(type="message_start", message=SimpleNamespace(usage=start))]
    for piece in turn.text:
        events.append(SimpleNamespace(type="content_block_delta", delta=SimpleNamespace(type="text_delta", text=piece)))
    for call_id, name, fragments in turn.calls:
        events.append(SimpleNamespace(type="content_block_start", content_block=SimpleNamespace(type="tool_use", id=call_id, name=name)))
        events.extend(SimpleNamespace(type="content_block_delta", delta=SimpleNamespace(type="input_json_delta", partial_json=f)) for f in fragments)
        events.append(SimpleNamespace(type="content_block_stop"))
    stop = "tool_use" if turn.calls else "end_turn"
    events.append(SimpleNamespace(type="message_delta", usage=SimpleNamespace(output_tokens=output), delta=SimpleNamespace(stop_reason=stop)))
    events.append(SimpleNamespace(type="message_stop"))
    return events


def _gemini_chunks(turn: Turn) -> list[Any]:
    prompt, output, cached = turn.usage
    usage = SimpleNamespace(prompt_token_count=prompt, candidates_token_count=output, cached_content_token_count=cached)
    parts = [SimpleNamespace(text=piece, function_call=None) for piece in turn.text]
    parts += [SimpleNamespace(text=None, function_call=SimpleNamespace(name=name, args=json.loads("".join(fragments)))) for _, name, fragments in turn.calls]
    return [SimpleNamespace(candidates=[SimpleNamespace(content=SimpleNamespace(parts=[part]))], usage_metadata=usage) for part in parts] or [
        SimpleNamespace(candidates=[], usage_metadata=usage)
    ]


def _responses_events(turn: Turn) -> list[Any]:
    prompt, output, cached = turn.usage
    events: list[Any] = [SimpleNamespace(type="response.output_text.delta", delta=piece) for piece in turn.text]
    for call_id, name, fragments in turn.calls:
        item = ResponseFunctionToolCall(id="fc_" + call_id, call_id=call_id, name=name, arguments="".join(fragments), type="function_call")
        events.append(SimpleNamespace(type="response.output_item.done", item=item))
    usage    = SimpleNamespace(input_tokens=prompt, input_tokens_details=SimpleNamespace(cached_tokens=cached), output_tokens=output)
    response = SimpleNamespace(status="completed", usage=usage, incomplete_details=None, error=None)
    events.append(SimpleNamespace(type="response.completed", response=response))
    return events


# ---------------------------------------------------------------------------
# Adapters under test
# ---------------------------------------------------------------------------


@dataclass
class Adapter:
    """How to build one provider over a fake client, and the failures its SDK raises."""

    name:     str
    build:    Callable[..., Any]                     # (effort) -> provider
    wire:     Callable[[Any, Any], AsyncMock]       # (provider, reply) -> the mock the request goes through
    payload:  Callable[[Turn], list[Any]]
    failure:  Callable[[int, str], Exception]
    retry_after_header: bool = True


def _http_error(cls: Any, status: int, message: str) -> Exception:
    response = httpx.Response(status, request=httpx.Request("POST", "https://api.example.test/v1/x"), headers={"retry-after": "9"})
    return cls(message, response=response, body=None)


_OPENAI_ERRORS    = {400: openai.BadRequestError, 401: openai.AuthenticationError, 429: openai.RateLimitError, 500: openai.InternalServerError}
_ANTHROPIC_ERRORS = {400: anthropic.BadRequestError, 401: anthropic.AuthenticationError, 429: anthropic.RateLimitError, 500: anthropic.InternalServerError}


def _genai_error(status: int, message: str) -> Exception:
    cls = genai_errors.ServerError if status >= 500 else genai_errors.ClientError
    return cls(status, {"error": {"message": message, "status": "ERR"}})


def _chat(effort: str = "") -> OpenAIProvider:
    return OpenAIProvider(ProviderConfig(provider=ProviderName.GROQ, model="m", api_key="k", reasoning_effort=effort))


def _responses(effort: str = "") -> OpenAIResponsesProvider:
    return OpenAIResponsesProvider(ProviderConfig(provider=ProviderName.OPENAI, model="gpt-5.6", api_key="k", reasoning_effort=effort))


def _anthropic(effort: str = "") -> AnthropicProvider:
    return AnthropicProvider(ProviderConfig(provider=ProviderName.ANTHROPIC, model="claude-sonnet-5-5", api_key="k", reasoning_effort=effort))


def _gemini(effort: str = "") -> GeminiProvider:
    return GeminiProvider(ProviderConfig(provider=ProviderName.GEMINI, model="gemini-3.6-flash", api_key="k", reasoning_effort=effort))


def _wire_chat(provider: Any, reply: Any) -> AsyncMock:
    create = AsyncMock(side_effect=reply) if isinstance(reply, Exception) else AsyncMock(return_value=_replay(reply))
    provider._client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    return create


def _wire_responses(provider: Any, reply: Any) -> AsyncMock:
    create = AsyncMock(side_effect=reply) if isinstance(reply, Exception) else AsyncMock(return_value=_replay(reply))
    provider._client = SimpleNamespace(responses=SimpleNamespace(create=create))
    return create


def _wire_anthropic(provider: Any, reply: Any) -> AsyncMock:
    create = AsyncMock(side_effect=reply) if isinstance(reply, Exception) else AsyncMock(return_value=_replay(reply))
    provider._client = SimpleNamespace(messages=SimpleNamespace(create=create))
    return create


def _wire_gemini(provider: Any, reply: Any) -> AsyncMock:
    create = AsyncMock(side_effect=reply) if isinstance(reply, Exception) else AsyncMock(return_value=_replay(reply))
    provider._client = SimpleNamespace(aio=SimpleNamespace(models=SimpleNamespace(generate_content_stream=create)))
    return create


ADAPTERS = [
    Adapter("openai-chat",      _chat,      _wire_chat,      _chat_chunks,      lambda s, m: _http_error(_OPENAI_ERRORS[s], s, m)),
    Adapter("openai-responses", _responses, _wire_responses, _responses_events, lambda s, m: _http_error(_OPENAI_ERRORS[s], s, m)),
    Adapter("anthropic",        _anthropic, _wire_anthropic, _anthropic_events, lambda s, m: _http_error(_ANTHROPIC_ERRORS[s], s, m)),
    Adapter("gemini",           _gemini,    _wire_gemini,    _gemini_chunks,    _genai_error, retry_after_header=False),
]
EVERY_ADAPTER = pytest.mark.parametrize("adapter", ADAPTERS, ids=[a.name for a in ADAPTERS])


async def _stream(adapter: Adapter, reply: Any, effort: str = "") -> tuple[list[ProviderEvent], AsyncMock]:
    provider = adapter.build(effort)
    create   = adapter.wire(provider, reply)
    events   = [e async for e in provider.stream("sys", [{"role": "user", "content": "go"}], [TOOL])]
    return events, create


def _types(events: list[ProviderEvent]) -> list[str]:
    return [e.type for e in events]


# ---------------------------------------------------------------------------
# Usage, text and stop
# ---------------------------------------------------------------------------


@EVERY_ADAPTER
async def test_usage_is_reported_once_after_the_content_and_before_done(adapter: Adapter) -> None:
    events, _ = await _stream(adapter, adapter.payload(Turn(text=["hel", "lo"])))
    assert _types(events).count("usage") == 1
    assert _types(events).count("done") == 1
    assert events[-1].type == "done"
    assert _types(events).index("usage") == len(events) - 2
    assert "".join(e.content for e in events if e.type == "content_delta") == "hello"
    usage = next(e.usage for e in events if e.type == "usage")
    assert usage == {"input_tokens": 100, "output_tokens": 20, "cache_read_tokens": 40}


@EVERY_ADAPTER
async def test_a_response_without_cached_tokens_reports_no_cache_read(adapter: Adapter) -> None:
    events, _ = await _stream(adapter, adapter.payload(Turn(text=["x"], usage=(50, 5, 0))))
    assert next(e.usage for e in events if e.type == "usage") == {"input_tokens": 50, "output_tokens": 5}


@EVERY_ADAPTER
async def test_a_text_response_ends_the_turn(adapter: Adapter) -> None:
    events, _ = await _stream(adapter, adapter.payload(Turn(text=["hi"])))
    assert events[-1].stop_reason == "end_turn"
    assert "tool_use_complete" not in _types(events)


# ---------------------------------------------------------------------------
# Tool calls
# ---------------------------------------------------------------------------


@EVERY_ADAPTER
async def test_a_tool_call_arrives_whole_with_its_id_name_and_input(adapter: Adapter) -> None:
    turn = Turn(text=["let me look"], calls=[("call_a", "Grep", ['{"pat', 'tern": ', '"x"}'])])
    events, _ = await _stream(adapter, adapter.payload(turn))
    (call,) = [e for e in events if e.type == "tool_use_complete"]
    assert call.tool_name == "Grep"
    assert call.tool_input_complete == {"pattern": "x"}
    assert call.tool_use_id
    assert events[-1].stop_reason == "tool_use"
    assert _types(events).index("tool_use_complete") < _types(events).index("done")


@EVERY_ADAPTER
async def test_parallel_calls_get_distinct_ids_and_keep_their_inputs(adapter: Adapter) -> None:
    turn = Turn(calls=[("call_a", "Grep", ['{"pattern": "a"}']), ("call_b", "Grep", ['{"pattern": ', '"b"}'])])
    events, _ = await _stream(adapter, adapter.payload(turn))
    calls = [e for e in events if e.type == "tool_use_complete"]
    assert len({c.tool_use_id for c in calls}) == 2
    assert [c.tool_input_complete for c in calls] == [{"pattern": "a"}, {"pattern": "b"}]


@pytest.mark.parametrize("adapter", [a for a in ADAPTERS if a.name != "gemini"], ids=lambda a: a.name)
async def test_the_ids_the_server_chose_are_passed_through(adapter: Adapter) -> None:
    turn = Turn(calls=[("call_a", "Grep", ['{"pattern": "a"}']), ("call_b", "Grep", ['{"pattern": "b"}'])])
    events, _ = await _stream(adapter, adapter.payload(turn))
    assert [e.tool_use_id for e in events if e.type == "tool_use_complete"] == ["call_a", "call_b"]


async def test_gemini_mints_a_unique_id_per_call_even_for_the_same_tool() -> None:
    adapter   = next(a for a in ADAPTERS if a.name == "gemini")
    turn      = Turn(calls=[("", "Grep", ['{"pattern": "a"}']), ("", "Grep", ['{"pattern": "a"}'])])
    events, _ = await _stream(adapter, adapter.payload(turn))
    ids       = [e.tool_use_id for e in events if e.type == "tool_use_complete"]
    assert len(set(ids)) == 2
    assert all(i.startswith("call_Grep_") for i in ids)


async def test_chat_keeps_one_call_when_a_server_repeats_a_chunk_and_two_when_it_reuses_an_index() -> None:
    adapter = next(a for a in ADAPTERS if a.name == "openai-chat")

    def chunk(call_id: str | None, args: str, finish: str | None = None) -> Any:
        call  = SimpleNamespace(index=0, id=call_id, function=SimpleNamespace(name="Grep" if call_id else None, arguments=args))
        delta = SimpleNamespace(content=None, tool_calls=[call] if args else None)
        return SimpleNamespace(choices=[SimpleNamespace(delta=delta, finish_reason=finish)], usage=None)

    events, _ = await _stream(adapter, [chunk("call_a", '{"pattern": "a"}'), chunk("call_b", '{"pattern": "b"}'), chunk(None, "", "tool_calls"), chunk(None, "", "tool_calls")])
    assert [e.tool_use_id for e in events if e.type == "tool_use_complete"] == ["call_a", "call_b"]
    assert _types(events).count("done") == 1


async def test_chat_estimates_usage_when_the_server_reports_none() -> None:
    adapter = next(a for a in ADAPTERS if a.name == "openai-chat")
    delta   = SimpleNamespace(content="a" * 40, tool_calls=None)
    events, _ = await _stream(adapter, [SimpleNamespace(choices=[SimpleNamespace(delta=delta, finish_reason="stop")], usage=None)])
    usage = next(e.usage for e in events if e.type == "usage")
    assert usage["output_tokens"] == 10 and usage["input_tokens"] > 0


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


@EVERY_ADAPTER
@pytest.mark.parametrize(("status", "message", "kind"), [
    (429, "slow down", RETRYABLE),
    (500, "server fell over", RETRYABLE),
    (401, "bad key", AUTH),
    (400, "prompt is too long: 300000 tokens", CONTEXT_LIMIT),
])
async def test_a_failed_request_becomes_one_classified_error(adapter: Adapter, status: int, message: str, kind: str) -> None:
    events, _ = await _stream(adapter, adapter.failure(status, message))
    assert _types(events) == ["error"]
    assert events[0].error_kind == kind
    assert events[0].status_code == status
    assert message in events[0].error


@EVERY_ADAPTER
async def test_retry_after_is_read_from_the_response_headers(adapter: Adapter) -> None:
    events, _ = await _stream(adapter, adapter.failure(429, "slow down"))
    assert events[0].retry_after == (9.0 if adapter.retry_after_header else None)


@EVERY_ADAPTER
async def test_a_transport_failure_is_retryable(adapter: Adapter) -> None:
    events, _ = await _stream(adapter, ConnectionError("connection reset"))
    assert (events[0].type, events[0].error_kind) == ("error", RETRYABLE)


# ---------------------------------------------------------------------------
# reasoning_effort
# ---------------------------------------------------------------------------


async def test_the_effort_is_a_top_level_field_on_chat_completions() -> None:
    adapter = next(a for a in ADAPTERS if a.name == "openai-chat")
    _, create = await _stream(adapter, [], effort="high")
    assert create.await_args.kwargs["reasoning_effort"] == "high"


async def test_the_effort_is_a_reasoning_object_on_the_responses_api() -> None:
    adapter = next(a for a in ADAPTERS if a.name == "openai-responses")
    _, create = await _stream(adapter, [], effort="high")
    assert create.await_args.kwargs["reasoning"]["effort"] == "high"
    assert "reasoning_effort" not in create.await_args.kwargs


async def test_the_effort_is_the_thinking_level_in_the_gemini_config() -> None:
    adapter = next(a for a in ADAPTERS if a.name == "gemini")
    _, create = await _stream(adapter, [], effort="high")
    assert str(create.await_args.kwargs["config"].thinking_config.thinking_level.value) == "HIGH"


async def test_anthropic_requests_do_not_carry_the_effort() -> None:
    adapter = next(a for a in ADAPTERS if a.name == "anthropic")
    _, create = await _stream(adapter, [], effort="high")
    assert "high" not in json.dumps(create.await_args.kwargs, default=str)
    assert "reasoning_effort" not in create.await_args.kwargs


# ---------------------------------------------------------------------------
# Image input
# ---------------------------------------------------------------------------

_IMAGE_TURN = [{"role": "user", "content": [{"type": "text", "text": "look"}, {"type": "image", "media_type": "image/png", "data": "QUJD"}]}]


def test_chat_completions_takes_images_as_data_urls() -> None:
    [_, user] = _chat()._convert_messages("sys", _IMAGE_TURN)
    assert user["content"][1] == {"type": "image_url", "image_url": {"url": "data:image/png;base64,QUJD"}}


def test_the_responses_api_takes_images_as_input_image_parts() -> None:
    [user] = convert_input(_IMAGE_TURN)
    assert user["content"][1] == {"type": "input_image", "image_url": "data:image/png;base64,QUJD", "detail": "auto"}


def test_anthropic_takes_images_as_base64_sources() -> None:
    [user] = _anthropic()._convert_messages(_IMAGE_TURN)
    assert user["content"][1] == {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "QUJD"}}


def test_gemini_takes_images_as_inline_data_bytes() -> None:
    [user] = _gemini()._convert_messages(_IMAGE_TURN)
    assert user["parts"][1] == {"inlineData": {"mimeType": "image/png", "data": b"ABC"}}
