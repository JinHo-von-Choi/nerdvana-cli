"""Gemini Interactions path: request, step conversion, thought replay, stream folding, status mapping, selection.

Every payload is written by hand with the typed models of the installed google-genai SDK; nothing talks to a server.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from google.genai import interactions
from pydantic import ValidationError

from nerdvana_cli.core.loop_state import LoopTurn
from nerdvana_cli.providers.base import ProviderConfig, ProviderEvent, ProviderName
from nerdvana_cli.providers.factory import create_provider
from nerdvana_cli.providers.gemini_interactions import GeminiInteractionsProvider, convert_input, uses_interactions
from nerdvana_cli.providers.gemini_provider import GeminiProvider

TOOL = SimpleNamespace(
    name             = "Grep",
    description_text = "Search files",
    input_schema     = {"type": "object", "properties": {"pattern": {"type": "string"}}, "required": ["pattern"]},
)


def _provider(**overrides: Any) -> GeminiInteractionsProvider:
    config = ProviderConfig(provider=ProviderName.GEMINI, model="gemini-3.6-flash", api_key="k", gemini_api="interactions", **overrides)
    return GeminiInteractionsProvider(config)


async def _replay(items: list[Any]) -> AsyncIterator[Any]:
    for item in items:
        yield item


def _wire(provider: GeminiInteractionsProvider, reply: Any) -> AsyncMock:
    create = AsyncMock(side_effect=reply) if isinstance(reply, Exception) else AsyncMock(return_value=reply)
    provider._client = SimpleNamespace(aio=SimpleNamespace(interactions=SimpleNamespace(create=create)))  # type: ignore[assignment]
    return create


async def _run(events: list[Any], **overrides: Any) -> list[ProviderEvent]:
    provider = _provider(**overrides)
    _wire(provider, _replay(events))
    return [e async for e in provider.stream("sys", [{"role": "user", "content": "go"}], [TOOL])]


def _start(index: int, step: Any) -> Any:
    return interactions.StepStart(index=index, step=step)


def _delta(index: int, delta: Any) -> Any:
    return interactions.StepDelta(index=index, delta=delta)


def _stop(index: int) -> Any:
    return interactions.StepStop(index=index)


def _completed(status: str = "completed", usage: Any = None) -> Any:
    return interactions.InteractionCompletedEvent(interaction=interactions.InteractionSseEventInteraction(id="v1_x", status=status, usage=usage))


def _call_steps(index: int, call_id: str, name: str, fragments: list[str]) -> list[Any]:
    start = interactions.FunctionCallStep(id=call_id, name=name, arguments={})
    return [_start(index, start), *(_delta(index, interactions.ArgumentsDelta(arguments=f)) for f in fragments), _stop(index)]


def _thought_steps(index: int, signature: str | None, summary: list[str]) -> list[Any]:
    steps = [_start(index, interactions.ThoughtStep())]
    steps.extend(_delta(index, interactions.ThoughtSummaryDelta(content=interactions.TextContent(text=t))) for t in summary)
    if signature:
        steps.append(_delta(index, interactions.ThoughtSignatureDelta(signature=signature)))
    steps.append(_stop(index))
    return steps


def _types(events: list[ProviderEvent]) -> list[str]:
    return [e.type for e in events]


# ---------------------------------------------------------------------------
# Selection and settings
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("api", "expected"), [("generate_content", False), ("interactions", True), ("auto", True)])
def test_the_setting_selects_the_api(api: str, expected: bool) -> None:
    assert uses_interactions(ProviderConfig(provider=ProviderName.GEMINI, gemini_api=api)) is expected


def test_the_factory_builds_the_variant_only_for_gemini() -> None:
    assert type(create_provider(provider="gemini", model="gemini-3.6-flash", api_key="k")) is GeminiProvider
    assert type(create_provider(provider="gemini", model="gemini-3.6-flash", api_key="k", gemini_api="interactions")) is GeminiInteractionsProvider
    assert type(create_provider(provider="gemini", model="gemini-3.6-flash", api_key="k", gemini_api="auto")) is GeminiInteractionsProvider
    assert type(create_provider(provider="anthropic", model="claude-sonnet-5-5", api_key="k", gemini_api="interactions")).__name__ == "AnthropicProvider"
    assert type(create_provider(provider="groq", model="m", api_key="k", gemini_api="interactions")).__name__ == "OpenAIProvider"


def test_the_setting_defaults_to_generate_content_and_reaches_the_provider() -> None:
    from nerdvana_cli.core.agent_loop import AgentLoop
    from nerdvana_cli.core.config.settings import ModelConfig, NerdvanaSettings

    assert ModelConfig().gemini_api == "generate_content"
    with pytest.raises(ValidationError):
        ModelConfig(gemini_api="soap")
    settings = NerdvanaSettings()
    settings.model.provider   = "gemini"
    settings.model.model      = "gemini-3.6-flash"
    settings.model.api_key    = "k"
    holder = SimpleNamespace(settings=settings)
    assert type(AgentLoop.create_provider_from_settings(holder)) is GeminiProvider  # type: ignore[arg-type]
    settings.model.gemini_api = "auto"
    assert type(AgentLoop.create_provider_from_settings(holder)) is GeminiInteractionsProvider  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Request
# ---------------------------------------------------------------------------

_HISTORY = [
    {"role": "user", "content": "go"},
    {"role": "assistant", "content": "[tool execution]", "tool_uses": [{"id": "c1", "name": "Grep", "input": {"pattern": "x"}}],
     "provider_blocks": [{"type": "thought", "signature": "QUJD", "summary": [{"type": "text", "text": "hm"}], "pos": 0}]},
    {"role": "tool", "tool_use_id": "c1", "content": "found", "is_error": True},
]


def test_the_request_is_stateless_and_valid_against_the_sdk_model() -> None:
    request = _provider(reasoning_effort="high")._request("be brief", _HISTORY, [TOOL])
    assert request["store"] is False
    assert "previous_interaction_id" not in request
    assert request["system_instruction"] == "be brief"
    assert request["tools"] == [{"type": "function", "name": "Grep", "description": "Search files", "parameters": TOOL.input_schema}]
    interactions.CreateModelInteraction.model_validate(request)


def test_generation_config_carries_the_limit_the_temperature_and_the_thinking_level() -> None:
    config = _provider(reasoning_effort="High", max_tokens=777, temperature=0.4)._generation_config()
    assert config == {"max_output_tokens": 777, "temperature": 0.4, "thinking_level": "high", "thinking_summaries": "auto"}


def test_summaries_are_not_requested_when_thinking_is_hidden_or_no_level_is_set() -> None:
    assert "thinking_summaries" not in _provider(reasoning_effort="low", show_thinking=False)._generation_config()
    assert "thinking_level" not in _provider()._generation_config()
    assert "thinking_summaries" not in _provider()._generation_config()


async def test_an_unknown_thinking_level_stops_the_request_with_an_error_event() -> None:
    provider = _provider(reasoning_effort="extreme")
    create   = _wire(provider, _replay([]))
    events   = [e async for e in provider.stream("s", [{"role": "user", "content": "go"}], [])]
    assert _types(events) == ["error"]
    assert "extreme" in events[0].error
    create.assert_not_awaited()


async def test_the_stream_flag_is_sent_only_by_stream() -> None:
    provider = _provider()
    create   = _wire(provider, _replay([_completed()]))
    [e async for e in provider.stream("s", [{"role": "user", "content": "go"}], [])]
    assert create.await_args.kwargs["stream"] is True
    assert create.await_args.kwargs["store"] is False
    create = _wire(provider, interactions.Interaction(id="v1", status="completed", steps=[]))
    await provider.send("s", [{"role": "user", "content": "go"}], [])
    assert "stream" not in create.await_args.kwargs
    assert create.await_args.kwargs["store"] is False


# ---------------------------------------------------------------------------
# Messages to steps
# ---------------------------------------------------------------------------


def test_a_tool_turn_becomes_thought_call_and_result_steps_paired_by_id() -> None:
    steps = convert_input(_HISTORY)
    assert steps == [
        {"type": "user_input", "content": [{"type": "text", "text": "go"}]},
        {"type": "thought", "signature": "QUJD", "summary": [{"type": "text", "text": "hm"}]},
        {"type": "function_call", "id": "c1", "name": "Grep", "arguments": {"pattern": "x"}},
        {"type": "function_result", "call_id": "c1", "name": "Grep", "result": [{"type": "text", "text": "found"}], "is_error": True},
    ]


def test_every_converted_step_is_accepted_by_the_sdk_input_types() -> None:
    steps = convert_input(_HISTORY + [{"role": "assistant", "content": "done"}, {"role": "user", "content": "more"}])
    interactions.CreateModelInteraction.model_validate({"model": "m", "input": steps})


def test_the_placeholder_text_of_a_tool_turn_is_not_sent_but_real_text_is() -> None:
    placeholder = convert_input([{"role": "assistant", "content": "[tool execution]", "tool_uses": [{"id": "c1", "name": "Grep", "input": {}}]}])
    assert [s["type"] for s in placeholder] == ["function_call"]
    spoken = convert_input([{"role": "assistant", "content": "looking", "tool_uses": [{"id": "c1", "name": "Grep", "input": {}}]}])
    assert [s["type"] for s in spoken] == ["model_output", "function_call"]


def test_an_assistant_turn_without_text_or_calls_adds_no_step() -> None:
    assert convert_input([{"role": "assistant", "content": ""}]) == []


def test_parallel_results_stay_separate_steps_in_call_order() -> None:
    calls   = [{"id": "c1", "name": "Grep", "input": {}}, {"id": "c2", "name": "Read", "input": {}}]
    history = [
        {"role": "assistant", "content": "", "tool_uses": calls},
        {"role": "tool", "tool_use_id": "c1", "content": "a"},
        {"role": "tool", "tool_use_id": "c2", "content": "b"},
    ]
    results = [s for s in convert_input(history) if s["type"] == "function_result"]
    assert [(r["call_id"], r["name"]) for r in results] == [("c1", "Grep"), ("c2", "Read")]
    assert not any("is_error" in r for r in results)


def test_a_result_with_no_known_function_is_sent_without_a_name() -> None:
    [step] = convert_input([{"role": "tool", "tool_use_id": "orphan", "content": "x"}])
    assert "name" not in step


def test_tool_results_inside_a_user_block_list_become_result_steps_between_the_other_blocks() -> None:
    history = [
        {"role": "assistant", "content": "", "tool_uses": [{"id": "c1", "name": "Grep", "input": {}}]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "c1", "content": "ok"},
            {"type": "text", "text": "and then"},
        ]},
    ]
    assert [s["type"] for s in convert_input(history)] == ["function_call", "function_result", "user_input"]


def test_the_signature_a_generate_content_call_stored_is_not_part_of_a_step() -> None:
    [step] = convert_input([{"role": "assistant", "content": "", "tool_uses": [{"id": "c1", "name": "Grep", "input": {}, "thought_signature": "QUJD"}]}])
    assert step == {"type": "function_call", "id": "c1", "name": "Grep", "arguments": {}}


def test_a_thought_without_a_signature_is_not_replayed() -> None:
    history = [{"role": "assistant", "content": "hi", "provider_blocks": [{"type": "thought", "summary": [{"type": "text", "text": "x"}]}, {"type": "thinking", "signature": "s"}]}]
    assert [s["type"] for s in convert_input(history)] == ["model_output"]


def test_thoughts_return_to_the_position_they_had_among_the_calls() -> None:
    blocks  = [
        {"type": "thought", "signature": "S0", "pos": 0},
        {"type": "thought", "signature": "S1", "pos": 1},
        {"type": "thought", "signature": "S2", "pos": 2},
    ]
    calls   = [{"id": "c1", "name": "Grep", "input": {}}, {"id": "c2", "name": "Grep", "input": {}}]
    steps   = convert_input([{"role": "assistant", "content": "", "tool_uses": calls, "provider_blocks": blocks}])
    order   = [s.get("signature") or s["id"] for s in steps]
    assert order == ["S0", "c1", "S1", "c2", "S2"]


# ---------------------------------------------------------------------------
# Stream folding
# ---------------------------------------------------------------------------


async def test_text_arrives_as_content_deltas_and_the_turn_ends() -> None:
    usage  = interactions.Usage(total_input_tokens=10, total_output_tokens=2)
    events = await _run([
        _start(0, interactions.ModelOutputStep()),
        _delta(0, interactions.TextDelta(text="he")),
        _delta(0, interactions.TextDelta(text="llo")),
        _stop(0),
        _completed(usage=usage),
    ])
    assert _types(events) == ["content_delta", "content_delta", "usage", "done"]
    assert events[-1].stop_reason == "end_turn"


async def test_text_that_comes_with_the_step_start_is_not_lost() -> None:
    events = await _run([_start(0, interactions.ModelOutputStep(content=[interactions.TextContent(text="whole")])), _stop(0), _completed()])
    assert [e.content for e in events if e.type == "content_delta"] == ["whole"]


async def test_a_thought_yields_thinking_text_and_a_replayable_block() -> None:
    events = await _run([*_thought_steps(0, "QUJD", ["plan ", "it"]), *_call_steps(1, "c1", "Grep", ['{"pattern": "x"}']), _completed("requires_action")])
    assert [e.thinking for e in events if e.type == "thinking_delta"] == ["plan ", "it"]
    (block,) = [e.block for e in events if e.type == "provider_block"]
    assert block == {"type": "thought", "signature": "QUJD", "pos": 0, "summary": [{"type": "text", "text": "plan "}, {"type": "text", "text": "it"}]}
    (call,) = [e for e in events if e.type == "tool_use_complete"]
    assert (call.tool_use_id, call.tool_name, call.tool_input_complete) == ("c1", "Grep", {"pattern": "x"})
    assert events[-1].stop_reason == "tool_use"


async def test_a_thought_that_follows_a_call_records_that_it_came_after_it() -> None:
    events = await _run([*_call_steps(0, "c1", "Grep", ["{}"]), *_thought_steps(1, "QUJD", []), _completed("requires_action")])
    (block,) = [e.block for e in events if e.type == "provider_block"]
    assert block["pos"] == 1
    assert "summary" not in block


async def test_a_thought_without_a_signature_gives_thinking_text_but_no_block() -> None:
    events = await _run([*_thought_steps(0, None, ["just text"]), _completed()])
    assert "provider_block" not in _types(events)
    assert "thinking_delta" in _types(events)


async def test_a_signature_given_on_the_step_itself_is_kept() -> None:
    events = await _run([_start(0, interactions.ThoughtStep(signature="WFla")), _stop(0), _completed()])
    (block,) = [e.block for e in events if e.type == "provider_block"]
    assert block["signature"] == "WFla"


async def test_arguments_given_whole_at_the_start_are_used_when_no_fragment_follows() -> None:
    start  = interactions.FunctionCallStep(id="c1", name="Grep", arguments={"pattern": "y"})
    events = await _run([_start(0, start), _stop(0), _completed("requires_action")])
    (call,) = [e for e in events if e.type == "tool_use_complete"]
    assert call.tool_input_complete == {"pattern": "y"}


async def test_unparseable_arguments_give_an_empty_input() -> None:
    events = await _run([*_call_steps(0, "c1", "Grep", ['{"pattern": ']), _completed("requires_action")])
    (call,) = [e for e in events if e.type == "tool_use_complete"]
    assert call.tool_input_complete == {}


async def test_calls_are_reported_whole_after_the_content_and_before_usage() -> None:
    usage  = interactions.Usage(total_input_tokens=5)
    events = await _run([*_call_steps(0, "c1", "Grep", ["{}"]), *_call_steps(1, "c2", "Grep", ["{}"]), _completed("requires_action", usage)])
    assert _types(events) == ["tool_use_complete", "tool_use_complete", "usage", "done"]


async def test_a_response_that_hit_the_output_limit_ends_as_max_tokens_and_drops_its_calls() -> None:
    events = await _run([
        _start(0, interactions.ModelOutputStep()),
        _delta(0, interactions.TextDelta(text="cut")),
        *_call_steps(1, "c1", "Grep", ['{"pattern": "x']),
        _completed("incomplete"),
    ])
    assert events[-1].stop_reason == "max_tokens"
    assert "tool_use_complete" not in _types(events)


@pytest.mark.parametrize("status", ["failed", "cancelled"])
async def test_a_failed_or_cancelled_interaction_is_an_error(status: str) -> None:
    events = await _run([_completed(status)])
    assert _types(events) == ["error"]
    assert status in events[0].error


async def test_a_stream_that_ends_without_completing_is_a_retryable_error() -> None:
    events = await _run([_start(0, interactions.ModelOutputStep())])
    assert (events[-1].type, events[-1].error_kind) == ("error", "retryable")


async def test_an_error_event_stops_the_stream_with_one_classified_error() -> None:
    events = await _run([
        _start(0, interactions.ModelOutputStep()),
        interactions.ErrorEvent(error=interactions.Error(code="x", message="rate limit exceeded, try later")),
        _completed(),
    ])
    assert _types(events) == ["error"]
    assert events[0].error_kind == "retryable"


async def test_events_the_adapter_does_not_read_are_ignored() -> None:
    events = await _run([
        interactions.InteractionCreatedEvent(interaction=interactions.InteractionSseEventInteraction(id="v1", status="in_progress")),
        interactions.InteractionStatusUpdate(interaction_id="v1", status="in_progress"),
        _completed(),
    ])
    assert _types(events) == ["done"]


async def test_thought_tokens_are_counted_as_output_and_cached_tokens_as_cache_reads() -> None:
    usage  = interactions.Usage(total_input_tokens=100, total_output_tokens=20, total_thought_tokens=30, total_cached_tokens=40)
    events = await _run([_completed(usage=usage)])
    assert next(e.usage for e in events if e.type == "usage") == {"input_tokens": 100, "output_tokens": 50, "cache_read_tokens": 40}


# ---------------------------------------------------------------------------
# Loop round trip
# ---------------------------------------------------------------------------


async def test_what_a_stream_delivers_is_what_the_next_request_replays() -> None:
    events = await _run([
        *_thought_steps(0, "QUJD", ["plan"]),
        *_call_steps(1, "c1", "Grep", ['{"pattern": "x"}']),
        *_call_steps(2, "c2", "Grep", ['{"pattern": "y"}']),
        _completed("requires_action"),
    ])
    turn = LoopTurn(messages=[], used_ids=set(), sent_count=0)
    for event in events:
        if event.type == "provider_block" and event.block:
            turn.provider_blocks.append(event.block)
        elif event.type == "tool_use_complete":
            turn.add_call(event.tool_use_id, event.tool_name, event.tool_input_complete, event.tool_signature)
    history = [
        {"role": "user", "content": "go"},
        {"role": "assistant", "content": "[tool execution]", "tool_uses": turn.tool_uses, "provider_blocks": turn.provider_blocks},
        *({"role": "tool", "tool_use_id": u["id"], "content": "ok"} for u in turn.tool_uses),
    ]
    assert [s["type"] for s in convert_input(history)] == ["user_input", "thought", "function_call", "function_call", "function_result", "function_result"]
    assert convert_input(history)[1]["signature"] == "QUJD"


# ---------------------------------------------------------------------------
# send
# ---------------------------------------------------------------------------


async def test_send_returns_text_calls_thoughts_usage_and_the_stop_reason() -> None:
    interaction = interactions.Interaction(
        id="v1", status="requires_action",
        steps=[
            interactions.ThoughtStep(signature="QUJD", summary=[interactions.TextContent(text="plan")]),
            interactions.ModelOutputStep(content=[interactions.TextContent(text="looking")]),
            interactions.FunctionCallStep(id="c1", name="Grep", arguments={"pattern": "x"}),
        ],
        usage=interactions.Usage(total_input_tokens=9, total_output_tokens=3),
    )
    provider = _provider()
    _wire(provider, interaction)
    result = await provider.send("s", [{"role": "user", "content": "go"}], [TOOL])
    assert result["content"] == "looking"
    assert result["tool_uses"] == [{"id": "c1", "name": "Grep", "input": {"pattern": "x"}}]
    assert result["stop_reason"] == "tool_use"
    assert result["usage"] == {"input_tokens": 9, "output_tokens": 3}
    assert result["provider_blocks"] == [{"type": "thought", "signature": "QUJD", "pos": 0, "summary": [{"type": "text", "text": "plan"}]}]


@pytest.mark.parametrize(("status", "stop"), [("completed", "end_turn"), ("incomplete", "max_tokens")])
async def test_send_maps_the_status_to_a_stop_reason(status: str, stop: str) -> None:
    provider = _provider()
    _wire(provider, interactions.Interaction(id="v1", status=status, steps=[]))
    result = await provider.send("s", [{"role": "user", "content": "go"}], [])
    assert result["stop_reason"] == stop
    assert result["usage"] == {}


async def test_send_reports_a_failed_interaction_and_a_raised_error_as_errors() -> None:
    provider = _provider()
    _wire(provider, interactions.Interaction(id="v1", status="failed", steps=[]))
    assert (await provider.send("s", [{"role": "user", "content": "go"}], []))["is_error"] is True
    _wire(provider, RuntimeError("boom"))
    result = await provider.send("s", [{"role": "user", "content": "go"}], [])
    assert result["is_error"] is True
    assert result["content"] == "boom"
