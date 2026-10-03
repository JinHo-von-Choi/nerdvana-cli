"""Anthropic effort: ``reasoning_effort`` as ``output_config.effort``, per-message changes, thinking tokens.

Payload shapes follow the effort and steering-thinking pages of the Anthropic documentation; nothing here
touches the network.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import anthropic
import pytest

try:
    import httpx2 as httpx  # the SDK's own HTTP layer from 1.0 on
except ImportError:          # pragma: no cover - older SDKs
    import httpx  # type: ignore[no-redef]

from nerdvana_cli.providers.anthropic_features import (
    BETA_TURN_EFFORT,
    EffortTracker,
    normalize_effort,
    supported_efforts,
    supports_turn_effort,
)
from nerdvana_cli.providers.anthropic_provider import AnthropicProvider, _usage_dict
from nerdvana_cli.providers.base import ProviderConfig, ProviderName

MODEL = "claude-sonnet-5-5"


class _Events:
    def __init__(self, events: list[Any]) -> None:
        self.events = events

    def __aiter__(self) -> AsyncIterator[Any]:
        async def _iter() -> AsyncIterator[Any]:
            for event in self.events:
                yield event
        return _iter()


def _ns(**kwargs: Any) -> SimpleNamespace:
    return SimpleNamespace(**kwargs)


def _provider(model: str = MODEL, effort: str = "", **config: Any) -> tuple[AnthropicProvider, AsyncMock]:
    provider = AnthropicProvider(ProviderConfig(provider=ProviderName.ANTHROPIC, api_key="k", model=model, reasoning_effort=effort, **config))
    create   = AsyncMock(side_effect=lambda **_: _Events([]))
    provider._client = _ns(messages=_ns(create=create))  # type: ignore[assignment]
    return provider, create


async def _request(provider: AnthropicProvider, messages: list[dict[str, Any]]) -> list[Any]:
    return [event async for event in provider.stream("sys", messages, [])]


def _user(text: str) -> dict[str, Any]:
    return {"role": "user", "content": text}


def _assistant(text: str) -> dict[str, Any]:
    return {"role": "assistant", "content": text}


# ---------------------------------------------------------------------------
# Which model takes which level
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("model", "levels"), [
    ("claude-sonnet-5-5", ("low", "medium", "high", "xhigh", "max")),
    ("claude-opus-5-5",   ("low", "medium", "high", "xhigh", "max")),
    ("claude-fable-5-1",  ("low", "medium", "high", "xhigh", "max")),
    ("claude-opus-4-8",   ("low", "medium", "high", "xhigh", "max")),
    ("claude-opus-4-6",   ("low", "medium", "high", "max")),
    ("claude-sonnet-4-6", ("low", "medium", "high", "max")),
    ("claude-mythos-preview", ("low", "medium", "high", "max")),
    ("claude-opus-4-5-20251101", ("low", "medium", "high")),
    ("claude-haiku-4-5-20251001", ()),
    ("claude-sonnet-4-5-20250929", ()),
    ("MiniMax-M2", ()),
])
def test_the_levels_a_model_accepts(model: str, levels: tuple[str, ...]) -> None:
    assert supported_efforts(model) == levels


@pytest.mark.parametrize(("model", "expected"), [
    ("claude-fable-5-1", True), ("claude-mythos-5-1", True), ("claude-opus-5-5", True), ("claude-opus-5", True),
    ("claude-sonnet-5-5", True), ("claude-fable-5", False), ("claude-sonnet-5", False), ("claude-opus-4-8", False),
])
def test_only_the_documented_models_take_a_per_message_effort(model: str, expected: bool) -> None:
    assert supports_turn_effort(model) is expected


def test_a_value_is_lower_cased_and_an_empty_one_means_nothing() -> None:
    assert normalize_effort(MODEL, " HIGH ") == "high"
    assert normalize_effort(MODEL, "") == ""


def test_a_model_without_effort_gets_nothing_whatever_the_value() -> None:
    assert normalize_effort("claude-haiku-4-5-20251001", "high") == ""
    assert normalize_effort("MiniMax-M2", "banana") == ""


@pytest.mark.parametrize(("model", "value"), [("claude-opus-4-6", "xhigh"), ("claude-sonnet-5-5", "none"), ("claude-sonnet-5-5", "adaptive")])
def test_a_value_the_model_does_not_accept_is_a_clear_error(model: str, value: str) -> None:
    with pytest.raises(ValueError, match=r"reasoning_effort .* is not an effort level of .* \(use one of: low, medium, high"):
        normalize_effort(model, value)


# ---------------------------------------------------------------------------
# The top-level value
# ---------------------------------------------------------------------------


async def test_the_configured_effort_goes_in_output_config() -> None:
    provider, create = _provider(effort="xhigh")
    await _request(provider, [_user("q")])
    assert create.await_args.kwargs["extra_body"] == {"output_config": {"effort": "xhigh"}}
    assert "extra_headers" not in create.await_args.kwargs


async def test_nothing_is_sent_when_no_effort_is_configured() -> None:
    provider, create = _provider()
    await _request(provider, [_user("q")])
    assert "extra_body" not in create.await_args.kwargs
    assert "extra_headers" not in create.await_args.kwargs


@pytest.mark.parametrize("model", ["claude-haiku-4-5-20251001", "MiniMax-M2"])
async def test_a_model_without_effort_gets_a_plain_request(model: str) -> None:
    provider, create = _provider(model, effort="high")
    await _request(provider, [_user("q")])
    assert "extra_body" not in create.await_args.kwargs


async def test_an_unsupported_value_fails_the_request_with_the_reason() -> None:
    provider, create = _provider("claude-opus-4-6", effort="xhigh")
    events = await _request(provider, [_user("q")])
    assert [e.type for e in events] == ["error"]
    assert "xhigh" in events[0].error
    assert "low, medium, high, max" in events[0].error
    create.assert_not_awaited()


async def test_send_reports_an_unsupported_value_too() -> None:
    provider, _ = _provider("claude-opus-4-6", effort="xhigh")
    result = await provider.send("sys", [_user("q")], [])
    assert result["is_error"] is True
    assert "xhigh" in result["content"]


async def test_effort_goes_with_the_adaptive_thinking_the_model_family_already_gets() -> None:
    provider, create = _provider(effort="high")
    await _request(provider, [_user("q")])
    kwargs = create.await_args.kwargs
    assert kwargs["thinking"] == {"type": "adaptive", "display": "summarized"}
    assert kwargs["extra_body"]["output_config"]["effort"] == "high"


async def test_effort_does_not_switch_thinking_on_for_a_model_that_needs_the_switch() -> None:
    provider, create = _provider("claude-opus-4-8", effort="max")
    await _request(provider, [_user("q")])
    kwargs = create.await_args.kwargs
    assert "thinking" not in kwargs
    assert kwargs["extra_body"]["output_config"]["effort"] == "max"


async def test_effort_goes_with_a_manual_budget_on_opus_4_5() -> None:
    provider, create = _provider("claude-opus-4-5-20251101", effort="low", extended_thinking=True, thinking_budget=2000)
    await _request(provider, [_user("q")])
    kwargs = create.await_args.kwargs
    assert kwargs["thinking"]["type"] == "enabled"
    assert kwargs["extra_body"]["output_config"]["effort"] == "low"


# ---------------------------------------------------------------------------
# Changing effort between turns
# ---------------------------------------------------------------------------


def _roles(kwargs: dict[str, Any]) -> list[str]:
    return [m["role"] for m in kwargs["messages"]]


async def test_a_level_set_before_the_first_request_becomes_the_top_level_value() -> None:
    provider, create = _provider(effort="high")
    assert provider.set_turn_effort("low") is True
    await _request(provider, [_user("q")])
    kwargs = create.await_args.kwargs
    assert kwargs["extra_body"]["output_config"]["effort"] == "low"
    assert _roles(kwargs) == ["user"]


async def test_a_later_change_is_an_effort_only_system_message_and_the_top_level_value_stays() -> None:
    provider, create = _provider(effort="high")
    await _request(provider, [_user("q1")])
    assert provider.set_turn_effort("low") is True
    await _request(provider, [_user("q1"), _assistant("a1"), _user("q2")])
    kwargs = create.await_args.kwargs
    assert _roles(kwargs) == ["user", "assistant", "system", "user"]
    assert kwargs["messages"][2] == {"role": "system", "content": [], "output_config": {"effort": "low"}}
    assert kwargs["extra_body"]["output_config"]["effort"] == "high"
    assert kwargs["extra_headers"] == {"anthropic-beta": BETA_TURN_EFFORT}


async def test_the_system_message_keeps_its_place_so_the_cached_prefix_still_matches() -> None:
    provider, create = _provider(effort="high")
    await _request(provider, [_user("q1")])
    provider.set_turn_effort("low")
    history = [_user("q1"), _assistant("a1"), _user("q2")]
    await _request(provider, history)
    first = create.await_args.kwargs["messages"]
    history += [_assistant("a2"), _user("q3")]
    await _request(provider, history)
    second = create.await_args.kwargs["messages"]
    assert [m["role"] for m in second] == ["user", "assistant", "system", "user", "assistant", "user"]
    assert second[:3] == first[:3]


async def test_each_further_change_adds_one_message_before_the_user_turn_it_applies_to() -> None:
    provider, create = _provider(effort="high")
    await _request(provider, [_user("q1")])
    provider.set_turn_effort("low")
    await _request(provider, [_user("q1"), _assistant("a1"), _user("q2")])
    provider.set_turn_effort("max")
    await _request(provider, [_user("q1"), _assistant("a1"), _user("q2"), _assistant("a2"), _user("q3")])
    messages = create.await_args.kwargs["messages"]
    levels   = [m["output_config"]["effort"] for m in messages if m["role"] == "system"]
    assert levels == ["low", "max"]
    assert _roles(create.await_args.kwargs) == ["user", "assistant", "system", "user", "assistant", "system", "user"]


async def test_repeating_the_level_in_force_adds_nothing() -> None:
    provider, create = _provider(effort="high")
    await _request(provider, [_user("q1")])
    provider.set_turn_effort("low")
    await _request(provider, [_user("q1"), _assistant("a1"), _user("q2")])
    provider.set_turn_effort("low")
    await _request(provider, [_user("q1"), _assistant("a1"), _user("q2"), _assistant("a2"), _user("q3")])
    assert _roles(create.await_args.kwargs).count("system") == 1


async def test_a_change_made_in_the_middle_of_a_tool_loop_waits_for_the_next_user_message() -> None:
    provider, create = _provider(effort="high")
    await _request(provider, [_user("q1")])
    provider.set_turn_effort("low")
    tool_turn = [
        _user("q1"),
        {"role": "assistant", "content": "[tool execution]", "tool_uses": [{"id": "t1", "name": "A", "input": {}}]},
        {"role": "tool", "content": "out", "tool_use_id": "t1"},
    ]
    await _request(provider, tool_turn)
    assert "system" not in _roles(create.await_args.kwargs)
    assert "extra_headers" not in create.await_args.kwargs
    await _request(provider, [*tool_turn, _assistant("done"), _user("q2")])
    assert _roles(create.await_args.kwargs)[-2:] == ["system", "user"]


async def test_a_model_without_per_message_effort_holds_the_first_level() -> None:
    provider, create = _provider("claude-sonnet-4-6", effort="high")
    await _request(provider, [_user("q1")])
    assert provider.set_turn_effort("low") is False
    await _request(provider, [_user("q1"), _assistant("a1"), _user("q2")])
    kwargs = create.await_args.kwargs
    assert kwargs["extra_body"]["output_config"]["effort"] == "high"
    assert "system" not in _roles(kwargs)
    assert "extra_headers" not in kwargs


async def test_a_model_without_effort_ignores_the_call() -> None:
    provider, create = _provider("claude-haiku-4-5-20251001")
    assert provider.set_turn_effort("high") is False
    await _request(provider, [_user("q")])
    assert "extra_body" not in create.await_args.kwargs


def test_a_level_the_model_does_not_accept_is_refused_when_set() -> None:
    provider, _ = _provider("claude-opus-4-6")
    with pytest.raises(ValueError, match="xhigh"):
        provider.set_turn_effort("xhigh")


def test_marks_beyond_a_shortened_history_are_dropped() -> None:
    tracker = EffortTracker(MODEL, "high")
    tracker.prepare([_user("q1")])
    tracker.set_turn("low")
    assert tracker.prepare([_user("q1"), _assistant("a1"), _user("q2")])[1] == {2: "low"}
    base, marks = tracker.prepare([_user("q1")])
    assert base == "high"
    assert marks == {0: "low"}


# ---------------------------------------------------------------------------
# What the real SDK puts on the wire (no network: a mock transport)
# ---------------------------------------------------------------------------


def _wired(responses: list[dict[str, Any]], captured: list[httpx.Request]) -> anthropic.AsyncAnthropic:
    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json=responses[min(len(captured), len(responses)) - 1])

    return anthropic.AsyncAnthropic(api_key="k", http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))


def _message(**usage: Any) -> dict[str, Any]:
    return {
        "id": "msg_1", "type": "message", "role": "assistant", "model": MODEL,
        "content": [{"type": "text", "text": "ok"}], "stop_reason": "end_turn", "stop_sequence": None,
        "usage": {"input_tokens": 10, "output_tokens": 20, **usage},
    }


async def test_the_sdk_sends_the_effort_the_system_message_and_the_beta_header() -> None:
    captured: list[httpx.Request] = []
    provider, _ = _provider(effort="high")
    provider._client = _wired([_message()], captured)
    await provider.send("sys", [_user("q1")], [])
    provider.set_turn_effort("low")
    await provider.send("sys", [_user("q1"), _assistant("a1"), _user("q2")], [])

    first, second = captured
    assert json.loads(first.content)["output_config"] == {"effort": "high"}
    assert "mid-conversation" not in first.headers.get("anthropic-beta", "")
    body = json.loads(second.content)
    assert body["output_config"] == {"effort": "high"}
    assert body["messages"][2] == {"role": "system", "content": [], "output_config": {"effort": "low"}}
    assert second.headers["anthropic-beta"] == BETA_TURN_EFFORT


# ---------------------------------------------------------------------------
# Thinking tokens
# ---------------------------------------------------------------------------


def test_thinking_tokens_are_recorded_beside_the_output_tokens() -> None:
    usage = _ns(input_tokens=25, output_tokens=348, output_tokens_details=_ns(thinking_tokens=312))
    assert _usage_dict(usage) == {"input_tokens": 25, "output_tokens": 348, "thinking_tokens": 312}


def test_a_usage_dict_without_the_details_has_no_thinking_key() -> None:
    assert "thinking_tokens" not in _usage_dict(_ns(input_tokens=1, output_tokens=2))
    assert "thinking_tokens" not in _usage_dict(_ns(input_tokens=1, output_tokens=2, output_tokens_details=_ns(thinking_tokens=0)))


def test_the_details_may_arrive_as_a_dict() -> None:
    usage = _ns(input_tokens=1, output_tokens=9, output_tokens_details={"thinking_tokens": 4})
    assert _usage_dict(usage)["thinking_tokens"] == 4


async def test_the_streamed_thinking_tokens_arrive_with_the_final_message_delta() -> None:
    provider, create = _provider()
    create.side_effect = lambda **_: _Events([
        _ns(type="message_start", message=_ns(usage=_ns(input_tokens=25, output_tokens=1))),
        _ns(type="message_delta", usage=_ns(output_tokens=348, output_tokens_details=_ns(thinking_tokens=312)), delta=_ns(stop_reason="end_turn")),
    ])
    events = await _request(provider, [_user("q")])
    usage  = next(e.usage for e in events if e.type == "usage")
    assert usage == {"input_tokens": 25, "output_tokens": 348, "thinking_tokens": 312}


async def test_send_reports_thinking_tokens_read_by_the_real_sdk() -> None:
    captured: list[httpx.Request] = []
    provider, _ = _provider()
    provider._client = _wired([_message(output_tokens_details={"thinking_tokens": 7})], captured)
    result = await provider.send("sys", [_user("q")], [])
    assert result["usage"]["thinking_tokens"] == 7


def test_the_loop_accepts_usage_with_extra_keys(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    from nerdvana_cli.core.agent_loop import AgentLoop
    from nerdvana_cli.core.config.settings import NerdvanaSettings
    from nerdvana_cli.core.session import SessionStorage
    from nerdvana_cli.core.tool import ToolRegistry

    monkeypatch.setenv("NERDVANA_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setattr(AgentLoop, "create_provider_from_settings", lambda self: object())
    settings     = NerdvanaSettings()
    settings.cwd = str(tmp_path)
    loop = AgentLoop(settings=settings, registry=ToolRegistry(), session=SessionStorage(session_id="t", storage_dir=str(tmp_path / "s")))
    seen: list[dict[str, Any]] = []
    loop.usage_listener = seen.append

    loop._apply_usage({"input_tokens": 100, "output_tokens": 50, "thinking_tokens": 30}, None)

    assert loop.state.usage.output_tokens == 50
    assert seen[0]["thinking_tokens"] == 30
