"""OpenAI Responses path: selection, request shape, input Items, stream events, usage and replay of reasoning.

The payloads are written by hand from the documented event and item shapes (see the header of
nerdvana_cli/providers/openai_responses.py); nothing here talks to a server.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import httpx
import openai
import pytest
from openai.types.responses import FunctionToolParam, ResponseFunctionToolCall, ResponseInputItemParam
from openai.types.responses.response_reasoning_item import ResponseReasoningItem, Summary
from pydantic import TypeAdapter

from nerdvana_cli.core.session import SessionStorage, messages_from_transcript
from nerdvana_cli.providers.base import ProviderConfig, ProviderEvent, ProviderName
from nerdvana_cli.providers.errors import RETRYABLE
from nerdvana_cli.providers.factory import create_provider
from nerdvana_cli.providers.openai_provider import OpenAIProvider
from nerdvana_cli.providers.openai_responses import OpenAIResponsesProvider, convert_input, uses_responses

OFFICIAL = "https://api.openai.com/v1"
TOOL     = SimpleNamespace(
    name             = "Grep",
    description_text = "Search files",
    input_schema     = {"type": "object", "properties": {"pattern": {"type": "string"}}, "required": ["pattern"]},
)


def _config(**fields: Any) -> ProviderConfig:
    base = {"provider": ProviderName.OPENAI, "model": "gpt-5.6", "api_key": "k", "base_url": OFFICIAL}
    return ProviderConfig(**{**base, **fields})


# ---------------------------------------------------------------------------
# Hand-written payloads
# ---------------------------------------------------------------------------


def _text(delta: str) -> Any:
    return SimpleNamespace(type="response.output_text.delta", delta=delta, item_id="msg_1", output_index=0, content_index=0, sequence_number=1)


def _summary(delta: str) -> Any:
    return SimpleNamespace(type="response.reasoning_summary_text.delta", delta=delta, item_id="rs_1", output_index=0, summary_index=0, sequence_number=1)


def _done(item: Any) -> Any:
    return SimpleNamespace(type="response.output_item.done", item=item, output_index=0, sequence_number=1)


def _reasoning(encrypted: str | None = "enc-blob", text: str = "weighed the options") -> ResponseReasoningItem:
    return ResponseReasoningItem(id="rs_1", summary=[Summary(text=text, type="summary_text")], type="reasoning", encrypted_content=encrypted)


def _call(call_id: str = "call_a", name: str = "Grep", arguments: str = '{"pattern": "x"}') -> ResponseFunctionToolCall:
    return ResponseFunctionToolCall(id="fc_1", call_id=call_id, name=name, arguments=arguments, type="function_call", status="completed")


def _usage(input_tokens: int = 120, output_tokens: int = 30, cached: int = 0) -> Any:
    return SimpleNamespace(
        input_tokens          = input_tokens,
        input_tokens_details  = SimpleNamespace(cached_tokens=cached),
        output_tokens         = output_tokens,
        output_tokens_details = SimpleNamespace(reasoning_tokens=10),
        total_tokens          = input_tokens + output_tokens,
    )


def _completed(usage: Any = None) -> Any:
    response = SimpleNamespace(status="completed", usage=usage if usage is not None else _usage(), incomplete_details=None, error=None)
    return SimpleNamespace(type="response.completed", response=response, sequence_number=9)


def _incomplete(reason: str) -> Any:
    response = SimpleNamespace(status="incomplete", usage=_usage(), incomplete_details=SimpleNamespace(reason=reason), error=None)
    return SimpleNamespace(type="response.incomplete", response=response, sequence_number=9)


def _failed(code: str, message: str) -> Any:
    response = SimpleNamespace(status="failed", usage=None, incomplete_details=None, error=SimpleNamespace(code=code, message=message))
    return SimpleNamespace(type="response.failed", response=response, sequence_number=9)


async def _run(events: list[Any], provider: OpenAIResponsesProvider | None = None, messages: list[dict[str, Any]] | None = None) -> list[ProviderEvent]:
    provider = provider or OpenAIResponsesProvider(_config())

    async def _stream() -> AsyncIterator[Any]:
        for event in events:
            yield event

    create = AsyncMock(return_value=_stream())
    provider._client = SimpleNamespace(responses=SimpleNamespace(create=create))  # type: ignore[assignment]
    return [e async for e in provider.stream("sys", messages or [{"role": "user", "content": "go"}], [TOOL])]


def _kinds(events: list[ProviderEvent]) -> list[str]:
    return [event.type for event in events]


# ---------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("provider", "base_url", "api", "expected"), [
    (ProviderName.OPENAI,   OFFICIAL,                       "auto",      True),
    (ProviderName.OPENAI,   "",                             "auto",      True),
    (ProviderName.OPENAI,   "http://localhost:4000/v1",     "auto",      False),
    (ProviderName.GROQ,     "https://api.groq.com/openai/v1", "auto",    False),
    (ProviderName.OLLAMA,   "http://localhost:11434/v1",    "auto",      False),
    (ProviderName.MINIMAX,  "https://api.minimax.io/v1",    "auto",      False),
    (ProviderName.OPENROUTER, "https://openrouter.ai/api/v1", "auto",    False),
    (ProviderName.DEEPSEEK, "https://api.deepseek.com",     "auto",      False),
    (ProviderName.OPENAI,   OFFICIAL,                       "chat",      False),
    (ProviderName.GROQ,     "https://api.groq.com/openai/v1", "responses", True),
    (ProviderName.OPENAI,   "http://localhost:4000/v1",     "responses", True),
])
def test_selection(provider: ProviderName, base_url: str, api: str, expected: bool) -> None:
    assert uses_responses(_config(provider=provider, base_url=base_url, openai_api=api)) is expected


def test_the_factory_builds_the_matching_class() -> None:
    assert type(create_provider(provider="openai", model="gpt-5.6", api_key="k")) is OpenAIResponsesProvider
    assert type(create_provider(provider="openai", model="gpt-5.6", api_key="k", openai_api="chat")) is OpenAIProvider
    assert type(create_provider(provider="groq", model="llama-3.3-70b-versatile", api_key="k")) is OpenAIProvider
    assert type(create_provider(provider="groq", model="m", api_key="k", openai_api="responses")) is OpenAIResponsesProvider
    assert type(create_provider(provider="anthropic", model="claude-sonnet-5-5", api_key="k", openai_api="responses")).__name__ == "AnthropicProvider"


def test_the_setting_defaults_to_auto_and_reaches_the_provider() -> None:
    from pydantic import ValidationError

    from nerdvana_cli.core.agent_loop import AgentLoop
    from nerdvana_cli.core.settings import ModelConfig, NerdvanaSettings

    assert ModelConfig().openai_api == "auto"
    with pytest.raises(ValidationError):
        ModelConfig(openai_api="soap")
    settings = NerdvanaSettings()
    settings.model.provider   = "openai"
    settings.model.model      = "gpt-5.6"
    settings.model.api_key    = "k"
    settings.model.openai_api = "chat"
    holder = SimpleNamespace(settings=settings)
    assert type(AgentLoop.create_provider_from_settings(holder)) is OpenAIProvider  # type: ignore[arg-type]
    settings.model.openai_api = "auto"
    assert type(AgentLoop.create_provider_from_settings(holder)) is OpenAIResponsesProvider  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Request
# ---------------------------------------------------------------------------


def test_the_request_is_stateless_and_carries_flat_tools() -> None:
    request = OpenAIResponsesProvider(_config())._request("be brief", [{"role": "user", "content": "q"}], [TOOL])
    assert request["store"] is False
    assert request["instructions"] == "be brief"
    assert request["max_output_tokens"] == 8192
    assert request["model"] == "gpt-5.6"
    assert "messages" not in request and "previous_response_id" not in request
    assert request["tools"] == [{
        "type": "function", "name": "Grep", "description": "Search files", "parameters": TOOL.input_schema, "strict": False,
    }]
    TypeAdapter(FunctionToolParam).validate_python(request["tools"][0])
    assert "reasoning" not in request and "include" not in request


def test_a_request_without_tools_or_system_prompt_omits_both() -> None:
    request = OpenAIResponsesProvider(_config())._request("", [{"role": "user", "content": "q"}], [])
    assert "tools" not in request and "instructions" not in request


def test_a_reasoning_effort_asks_for_a_summary_and_encrypted_reasoning() -> None:
    request = OpenAIResponsesProvider(_config(reasoning_effort="high"))._request("s", [], [TOOL])
    assert request["reasoning"] == {"effort": "high", "summary": "auto"}
    assert request["include"] == ["reasoning.encrypted_content"]
    assert "temperature" not in request


def test_no_summary_is_requested_when_thinking_is_hidden() -> None:
    request = OpenAIResponsesProvider(_config(reasoning_effort="low", show_thinking=False))._request("s", [], [TOOL])
    assert request["reasoning"] == {"effort": "low"}


def test_effort_none_sends_the_effort_and_keeps_sampling() -> None:
    request = OpenAIResponsesProvider(_config(reasoning_effort="none", temperature=0.3))._request("s", [], [TOOL])
    assert request["reasoning"] == {"effort": "none"}
    assert "include" not in request
    assert request["temperature"] == 0.3


# ---------------------------------------------------------------------------
# Input Items
# ---------------------------------------------------------------------------


def _history() -> list[dict[str, Any]]:
    block = {"type": "reasoning", "id": "rs_1", "summary": [{"type": "summary_text", "text": "t"}], "encrypted_content": "enc-blob"}
    return [
        {"role": "user", "content": "find x"},
        {"role": "assistant", "content": "[tool execution]", "tool_uses": [{"id": "call_a", "name": "Grep", "input": {"pattern": "x"}}], "provider_blocks": [block]},
        {"role": "tool", "content": "a.py:1", "tool_use_id": "call_a", "is_error": False},
        {"role": "assistant", "content": "found it", "provider_blocks": [{"type": "thinking", "thinking": "from another provider", "signature": "s"}]},
        {"role": "user", "content": "thanks"},
    ]


def test_messages_become_items_and_the_call_result_round_trips_by_call_id() -> None:
    items = convert_input(_history())
    assert items == [
        {"role": "user", "content": "find x"},
        {"type": "reasoning", "id": "rs_1", "summary": [{"type": "summary_text", "text": "t"}], "encrypted_content": "enc-blob"},
        {"type": "function_call", "call_id": "call_a", "name": "Grep", "arguments": '{"pattern": "x"}'},
        {"type": "function_call_output", "call_id": "call_a", "output": "a.py:1"},
        {"role": "assistant", "content": "found it"},
        {"role": "user", "content": "thanks"},
    ]
    TypeAdapter(list[ResponseInputItemParam]).validate_python(items)


def test_a_call_is_sent_without_the_item_id_and_with_json_arguments() -> None:
    items = convert_input([{"role": "assistant", "content": "", "tool_uses": [{"id": "c1", "name": "Edit", "input": {"s": "ä\n"}}]}])
    assert "id" not in items[0]
    assert json.loads(items[0]["arguments"]) == {"s": "ä\n"}


def test_images_become_input_parts() -> None:
    blocks = [{"type": "text", "text": "what is this"}, {"type": "image", "media_type": "image/png", "data": "QUJD"}]
    [item] = convert_input([{"role": "user", "content": blocks}])
    assert item["content"] == [
        {"type": "input_text", "text": "what is this"},
        {"type": "input_image", "image_url": "data:image/png;base64,QUJD", "detail": "auto"},
    ]
    TypeAdapter(list[ResponseInputItemParam]).validate_python([item])


def test_reasoning_survives_a_session_transcript(tmp_path: Any) -> None:
    storage = SessionStorage(session_id="s1", storage_dir=str(tmp_path))
    block   = {"type": "reasoning", "id": "rs_1", "summary": [], "encrypted_content": "enc-blob"}
    storage.record_user_message("find x")
    storage.record_assistant_message("", [{"id": "call_a", "name": "Grep", "input": {"pattern": "x"}}], [block])
    storage.record_tool_result("Grep", "call_a", "a.py:1")
    entries  = [json.loads(line) for line in (tmp_path / "s1.jsonl").read_text(encoding="utf-8").splitlines()]
    restored = messages_from_transcript(entries)
    assert restored[1].provider_blocks == [block]
    items = convert_input([{"role": "assistant", "content": m.content, "tool_uses": m.tool_uses, "provider_blocks": m.provider_blocks} for m in restored[1:2]])
    assert items[0] == block


# ---------------------------------------------------------------------------
# Stream
# ---------------------------------------------------------------------------


async def test_text_reasoning_calls_usage_and_stop_map_to_provider_events() -> None:
    events = await _run([
        _summary("weighing "), _summary("options"),
        _done(_reasoning()),
        _text("Let me "), _text("look."),
        _done(_call()),
        _completed(_usage(120, 30, cached=100)),
    ])
    assert _kinds(events) == ["thinking_delta", "thinking_delta", "provider_block", "content_delta", "content_delta", "tool_use_complete", "usage", "done"]
    assert "".join(e.thinking for e in events if e.type == "thinking_delta") == "weighing options"
    assert "".join(e.content for e in events if e.type == "content_delta") == "Let me look."
    block = next(e.block for e in events if e.type == "provider_block")
    assert block == {"type": "reasoning", "id": "rs_1", "summary": [{"type": "summary_text", "text": "weighed the options"}], "encrypted_content": "enc-blob"}
    call = next(e for e in events if e.type == "tool_use_complete")
    assert (call.tool_use_id, call.tool_name, call.tool_input_complete) == ("call_a", "Grep", {"pattern": "x"})
    assert next(e.usage for e in events if e.type == "usage") == {"input_tokens": 120, "output_tokens": 30, "cache_read_tokens": 100}
    assert events[-1].stop_reason == "tool_use"


async def test_a_text_only_response_ends_the_turn_with_usage_once() -> None:
    events = await _run([_text("hi"), _completed()])
    assert _kinds(events) == ["content_delta", "usage", "done"]
    assert events[-1].stop_reason == "end_turn"
    assert next(e.usage for e in events if e.type == "usage") == {"input_tokens": 120, "output_tokens": 30}


async def test_think_tags_in_the_text_are_split_off() -> None:
    events = await _run([_text("<think>hm</think>ans"), _completed()])
    assert "".join(e.content for e in events if e.type == "content_delta") == "ans"
    assert "".join(e.thinking for e in events if e.type == "thinking_delta") == "hm"


async def test_parallel_calls_are_kept_apart_and_a_repeated_item_is_reported_once() -> None:
    events = await _run([
        _done(_call("call_a", arguments='{"pattern": "a"}')),
        _done(_call("call_b", arguments='{"pattern": "b"}')),
        _done(_call("call_b", arguments='{"pattern": "b"}')),
        _completed(),
    ])
    calls = [e for e in events if e.type == "tool_use_complete"]
    assert [(c.tool_use_id, c.tool_input_complete) for c in calls] == [("call_a", {"pattern": "a"}), ("call_b", {"pattern": "b"})]


async def test_unparseable_arguments_become_an_empty_input() -> None:
    events = await _run([_done(_call(arguments='{"pattern": ')), _completed()])
    assert next(e for e in events if e.type == "tool_use_complete").tool_input_complete == {}


async def test_reasoning_without_encrypted_content_is_not_carried() -> None:
    events = await _run([_done(_reasoning(encrypted=None)), _text("x"), _completed()])
    assert "provider_block" not in _kinds(events)


async def test_a_truncated_response_stops_with_max_tokens_and_runs_no_call() -> None:
    events = await _run([_text("par"), _done(_call(arguments='{"pat')), _incomplete("max_output_tokens")])
    assert "tool_use_complete" not in _kinds(events)
    assert events[-1].stop_reason == "max_tokens"


async def test_a_filtered_response_reports_its_reason() -> None:
    events = await _run([_incomplete("content_filter")])
    assert events[-1].stop_reason == "content_filter"


@pytest.mark.parametrize(("code", "kind"), [("server_error", RETRYABLE), ("rate_limit_exceeded", RETRYABLE), ("invalid_prompt", "other")])
async def test_a_failed_response_becomes_a_classified_error(code: str, kind: str) -> None:
    events = await _run([_text("a"), _failed(code, "boom")])
    assert events[-1].type == "error"
    assert (events[-1].error, events[-1].error_kind) == ("boom", kind)
    assert "done" not in _kinds(events) and "usage" not in _kinds(events)


async def test_an_error_event_ends_the_stream() -> None:
    events = await _run([SimpleNamespace(type="error", code="server_error", message="overloaded", param=None, sequence_number=3), _text("late")])
    assert events[-1].type == "error" and events[-1].error_kind == RETRYABLE


async def test_a_stream_that_stops_early_is_an_error() -> None:
    events = await _run([_text("half")])
    assert events[-1].type == "error"
    assert events[-1].error_kind == RETRYABLE


async def test_unrelated_events_are_ignored() -> None:
    noise = [SimpleNamespace(type=t, sequence_number=1) for t in ("response.created", "response.in_progress", "response.output_item.added", "response.content_part.added")]
    assert _kinds(await _run([*noise, _completed()])) == ["usage", "done"]


async def test_a_request_failure_is_classified_like_the_chat_path() -> None:
    provider = OpenAIResponsesProvider(_config())
    request  = httpx.Request("POST", OFFICIAL + "/responses")
    error    = openai.RateLimitError("slow down", response=httpx.Response(429, request=request, headers={"retry-after": "7"}), body=None)
    provider._client = SimpleNamespace(responses=SimpleNamespace(create=AsyncMock(side_effect=error)))  # type: ignore[assignment]
    [event] = [e async for e in provider.stream("sys", [{"role": "user", "content": "q"}], [])]
    assert (event.type, event.error_kind, event.status_code, event.retry_after) == ("error", RETRYABLE, 429, 7.0)


async def test_the_create_call_streams_with_store_off() -> None:
    provider = OpenAIResponsesProvider(_config())

    async def _empty() -> AsyncIterator[Any]:
        return
        yield

    create = AsyncMock(return_value=_empty())
    provider._client = SimpleNamespace(responses=SimpleNamespace(create=create))  # type: ignore[assignment]
    _ = [e async for e in provider.stream("sys", [{"role": "user", "content": "q"}], [TOOL])]
    assert create.await_args.kwargs["stream"] is True
    assert create.await_args.kwargs["store"] is False


# ---------------------------------------------------------------------------
# send
# ---------------------------------------------------------------------------


async def test_send_collects_text_calls_reasoning_and_usage() -> None:
    message  = SimpleNamespace(type="message", content=[SimpleNamespace(type="output_text", text="ok ")], role="assistant")
    response = SimpleNamespace(output=[_reasoning(), message, _call()], incomplete_details=None, usage=_usage(50, 5, cached=20))
    provider = OpenAIResponsesProvider(_config())
    create   = AsyncMock(return_value=response)
    provider._client = SimpleNamespace(responses=SimpleNamespace(create=create))  # type: ignore[assignment]
    result = await provider.send("sys", [{"role": "user", "content": "q"}], [TOOL])
    assert result["content"] == "ok "
    assert result["tool_uses"] == [{"id": "call_a", "name": "Grep", "input": {"pattern": "x"}}]
    assert result["stop_reason"] == "tool_use"
    assert result["usage"] == {"input_tokens": 50, "output_tokens": 5, "cache_read_tokens": 20}
    assert result["provider_blocks"][0]["encrypted_content"] == "enc-blob"
    assert create.await_args.kwargs["store"] is False
    assert "stream" not in create.await_args.kwargs


async def test_send_reports_a_failure_as_an_error_result() -> None:
    provider = OpenAIResponsesProvider(_config())
    provider._client = SimpleNamespace(responses=SimpleNamespace(create=AsyncMock(side_effect=RuntimeError("down"))))  # type: ignore[assignment]
    assert await provider.send("sys", [], []) == {"content": "down", "is_error": True}
