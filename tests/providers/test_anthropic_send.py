"""AnthropicProvider.send/_convert_messages/list_models 단위 테스트."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from nerdvana_cli.providers.anthropic_provider import AnthropicProvider
from nerdvana_cli.providers.base import ProviderConfig


def _provider() -> AnthropicProvider:
    return AnthropicProvider(
        ProviderConfig(
            provider="anthropic",
            model="claude-sonnet-4-20250514",
            api_key="test-key",
            max_tokens=100,
            temperature=1.0,
        )
    )


def _block(kind: str, **kw: Any) -> SimpleNamespace:
    return SimpleNamespace(type=kind, **kw)


async def test_send_returns_text_and_tool_uses(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = _provider()
    response = SimpleNamespace(
        content=[
            _block("text", text="hello "),
            _block("text", text="world"),
            _block("tool_use", id="t1", name="FileRead", input={"path": "a.py"}),
        ],
        stop_reason="tool_use",
        usage=SimpleNamespace(input_tokens=10, output_tokens=5),
    )
    client = SimpleNamespace(messages=SimpleNamespace(create=AsyncMock(return_value=response)))
    monkeypatch.setattr(provider, "_get_client", lambda: client)

    result = await provider.send("sys", [{"role": "user", "content": "hi"}], [])

    assert result["content"] == "hello world"
    assert result["tool_uses"] == [{"id": "t1", "name": "FileRead", "input": {"path": "a.py"}}]
    assert result["stop_reason"] == "tool_use"
    assert result["usage"] == {"input_tokens": 10, "output_tokens": 5}


async def test_send_text_only_yields_empty_tool_uses(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = _provider()
    response = SimpleNamespace(
        content=[_block("text", text="done")],
        stop_reason="end_turn",
        usage=SimpleNamespace(input_tokens=3, output_tokens=1),
    )
    client = SimpleNamespace(messages=SimpleNamespace(create=AsyncMock(return_value=response)))
    monkeypatch.setattr(provider, "_get_client", lambda: client)

    result = await provider.send("sys", [], [])

    assert result["content"] == "done"
    assert result["tool_uses"] == []
    assert result["stop_reason"] == "end_turn"
    assert result["usage"] == {"input_tokens": 3, "output_tokens": 1}


async def test_send_exception_returns_error(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = _provider()
    client = SimpleNamespace(
        messages=SimpleNamespace(create=AsyncMock(side_effect=RuntimeError("boom")))
    )
    monkeypatch.setattr(provider, "_get_client", lambda: client)

    result = await provider.send("sys", [], [])

    assert result["is_error"] is True
    assert "boom" in result["content"]


async def test_send_import_error(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = _provider()

    def _raise_import() -> Any:
        raise ImportError("no anthropic")

    monkeypatch.setattr(provider, "_get_client", _raise_import)

    result = await provider.send("sys", [], [])

    assert result["is_error"] is True
    assert "not installed" in result["content"]


def test_convert_messages_pass_through_plain_roles() -> None:
    provider = _provider()
    parts = [{"type": "text", "text": "part1"}, {"type": "text", "text": "part2"}]
    out = provider._convert_messages(
        [
            {"role": "user", "content": "plain"},
            {"role": "assistant", "content": "answer"},
            {"role": "user", "content": parts},
        ]
    )
    assert out[0] == {"role": "user", "content": [{"type": "text", "text": "plain"}]}
    assert out[1] == {"role": "assistant", "content": [{"type": "text", "text": "answer"}]}
    assert out[2]["content"] == parts


def test_convert_messages_tool_result_block() -> None:
    provider = _provider()
    out = provider._convert_messages(
        [
            {
                "role": "tool",
                "content": "tool output",
                "tool_use_id": "t1",
                "is_error": True,
            }
        ]
    )
    assert out == [
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "t1",
                    "content": "tool output",
                    "is_error": True,
                }
            ],
        }
    ]


async def test_list_models_failure_returns_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = _provider()
    client = SimpleNamespace(models=SimpleNamespace(list=AsyncMock(side_effect=RuntimeError("x"))))
    monkeypatch.setattr(provider, "_get_client", lambda: client)
    assert await provider.list_models() == []


async def test_list_models_success_sorted(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = _provider()
    response = SimpleNamespace(
        data=[
            SimpleNamespace(id="claude-z", display_name="Z", created_at="2026-01-01"),
            SimpleNamespace(id="claude-a", created_at="2025-01-01"),
        ]
    )
    client = SimpleNamespace(models=SimpleNamespace(list=AsyncMock(return_value=response)))
    monkeypatch.setattr(provider, "_get_client", lambda: client)

    models = await provider.list_models()

    assert [m.id for m in models] == ["claude-a", "claude-z"]
    assert models[0].name == "claude-a"
    assert models[1].name == "Z"
