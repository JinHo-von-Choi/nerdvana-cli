"""OpenAIProvider.send/_convert_messages/_safe_str/_build_tools 단위 테스트."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from nerdvana_cli.providers.base import ProviderConfig
from nerdvana_cli.providers.openai_provider import (
    OpenAIProvider,
    _is_stream_options_unsupported,
    _safe_str,
)


def _provider() -> OpenAIProvider:
    return OpenAIProvider(
        ProviderConfig(
            provider="openai",
            model="gpt-4.1",
            api_key="test-key",
            max_tokens=100,
            temperature=1.0,
        )
    )


def _choice(content: str | None, tool_calls: list[Any] | None = None, finish: str = "stop") -> SimpleNamespace:
    return SimpleNamespace(
        message=SimpleNamespace(content=content, tool_calls=tool_calls),
        finish_reason=finish,
    )


def _client(response: Any = None, side_effect: Exception | None = None) -> SimpleNamespace:
    create = AsyncMock(side_effect=side_effect) if side_effect is not None else AsyncMock(return_value=response)
    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))


async def test_send_text_only(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = _provider()
    response = SimpleNamespace(
        choices=[_choice("hi there")],
        usage=SimpleNamespace(prompt_tokens=7, completion_tokens=3),
    )
    monkeypatch.setattr(provider, "_get_client", lambda: _client(response))

    result = await provider.send("sys", [{"role": "user", "content": "q"}], [])

    assert result["content"] == "hi there"
    assert result["tool_uses"] == []
    assert result["stop_reason"] == "stop"
    assert result["usage"] == {"input_tokens": 7, "output_tokens": 3}


async def test_send_tool_call_invalid_json_args(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = _provider()
    tc = SimpleNamespace(
        id="c1",
        function=SimpleNamespace(name="Grep", arguments="{not json"),
    )
    response = SimpleNamespace(choices=[_choice(None, [tc], finish="tool_calls")], usage=None)
    monkeypatch.setattr(provider, "_get_client", lambda: _client(response))

    result = await provider.send("sys", [], [])

    assert result["tool_uses"] == [{"id": "c1", "name": "Grep", "input": {}}]
    assert result["usage"] == {}
    assert result["stop_reason"] == "tool_calls"


async def test_send_valid_json_tool_args(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = _provider()
    tc = SimpleNamespace(
        id="c2",
        function=SimpleNamespace(name="FileRead", arguments=json.dumps({"path": "x.py"})),
    )
    response = SimpleNamespace(choices=[_choice(None, [tc])], usage=None)
    monkeypatch.setattr(provider, "_get_client", lambda: _client(response))

    result = await provider.send("sys", [], [])

    assert result["tool_uses"][0]["input"] == {"path": "x.py"}


async def test_send_empty_tool_arguments(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = _provider()
    tc = SimpleNamespace(
        id="c3",
        function=SimpleNamespace(name="NoArgs", arguments=""),
    )
    response = SimpleNamespace(choices=[_choice(None, [tc])], usage=None)
    monkeypatch.setattr(provider, "_get_client", lambda: _client(response))

    result = await provider.send("sys", [], [])

    assert result["tool_uses"][0]["input"] == {}


async def test_send_generic_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = _provider()
    monkeypatch.setattr(provider, "_get_client", lambda: _client(side_effect=RuntimeError("rate limited")))

    result = await provider.send("sys", [], [])

    assert result["is_error"] is True
    assert "rate limited" in result["content"]


async def test_send_unicode_decode_error(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = _provider()
    err = UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")
    monkeypatch.setattr(provider, "_get_client", lambda: _client(side_effect=err))

    result = await provider.send("sys", [], [])

    assert result["is_error"] is True
    assert "UTF-8 decoding error" in result["content"]


async def test_send_import_error(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = _provider()

    def _raise() -> Any:
        raise ImportError("no openai")

    monkeypatch.setattr(provider, "_get_client", _raise)
    result = await provider.send("sys", [], [])
    assert result["is_error"] is True
    assert "not installed" in result["content"]


async def test_send_multi_choice_content_concat(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = _provider()
    response = SimpleNamespace(
        choices=[_choice("part1 "), _choice("part2")],
        usage=SimpleNamespace(prompt_tokens=1, completion_tokens=2),
    )
    monkeypatch.setattr(provider, "_get_client", lambda: _client(response))

    result = await provider.send("sys", [], [])

    assert result["content"] == "part1 part2"
    assert result["stop_reason"] == "stop"


async def test_send_empty_choices_stop_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = _provider()
    response = SimpleNamespace(choices=[], usage=None)
    monkeypatch.setattr(provider, "_get_client", lambda: _client(response))

    result = await provider.send("sys", [], [])

    assert result["content"] == ""
    assert result["tool_uses"] == []
    assert result["stop_reason"] == "stop"
    assert result["usage"] == {}


async def test_send_usage_none(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = _provider()
    response = SimpleNamespace(choices=[_choice("x")], usage=None)
    monkeypatch.setattr(provider, "_get_client", lambda: _client(response))

    result = await provider.send("sys", [], [])

    assert result["usage"] == {}


def test_safe_str_handles_weird_objects() -> None:
    class Explodes:
        def __str__(self) -> str:
            raise ValueError("nope")

    assert _safe_str(Explodes()) == ""
    assert _safe_str("ok") == "ok"
    assert _safe_str(None) == ""


def test_convert_messages_includes_system_and_is_error() -> None:
    provider = _provider()
    out = provider._convert_messages(
        "system text",
        [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "ok", "is_error": True},
        ],
    )
    assert out[0]["role"] == "system"
    assert out[0]["content"] == "system text"
    assert out[1]["role"] == "user"
    assert out[2] == {"role": "assistant", "content": "ok"}


def test_convert_messages_empty_system_prompt_skips_header() -> None:
    provider = _provider()
    out = provider._convert_messages("", [{"role": "user", "content": "hi"}])
    assert out == [{"role": "user", "content": "hi"}]


def test_convert_messages_tool_and_assistant_tool_uses() -> None:
    provider = _provider()
    out = provider._convert_messages(
        "sys",
        [
            {"role": "tool", "content": "tool output", "tool_use_id": "t1"},
            {"role": "assistant", "content": "", "tool_uses": [{"id": "a1", "name": "Grep", "input": {"q": "x"}}]},
            {"role": "user", "content": 42},
        ],
    )
    assert out[1] == {"role": "tool", "content": "tool output", "tool_call_id": "t1"}
    assert out[2]["role"] == "assistant"
    assert out[2]["content"] is None
    assert out[2]["tool_calls"][0]["function"]["name"] == "Grep"
    assert json.loads(out[2]["tool_calls"][0]["function"]["arguments"]) == {"q": "x"}
    assert out[3] == {"role": "user", "content": "42"}


def test_convert_messages_tool_uses_unserializable_input() -> None:
    provider = _provider()
    out = provider._convert_messages(
        "sys",
        [{"role": "assistant", "content": "s", "tool_uses": [{"id": "a1", "name": "T", "input": {1, 2}}]}],
    )
    assert out[1]["tool_calls"][0]["function"]["arguments"] == "{}"


def test_build_tools_empty_list_returns_empty() -> None:
    provider = _provider()
    assert provider._build_tools([]) == []


def test_is_stream_options_unsupported_shapes() -> None:
    assert _is_stream_options_unsupported(TypeError("unexpected keyword")) is True
    assert _is_stream_options_unsupported(SimpleNamespace(status_code=400)) is True
    assert _is_stream_options_unsupported(SimpleNamespace(status_code=422)) is True
    assert _is_stream_options_unsupported(SimpleNamespace(status_code=500)) is False
    assert _is_stream_options_unsupported(RuntimeError("boom")) is False
