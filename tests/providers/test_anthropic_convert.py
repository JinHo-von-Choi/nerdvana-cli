"""AnthropicProvider._convert_messages: tool_use and tool_result pairing.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

from typing import Any

from nerdvana_cli.providers.anthropic_provider import AnthropicProvider
from nerdvana_cli.providers.base import ProviderConfig, ProviderName


def _provider() -> AnthropicProvider:
    return AnthropicProvider(ProviderConfig(provider=ProviderName.ANTHROPIC, api_key="k", model="m"))


def _use(tool_id: str, name: str = "FileRead", **input_: Any) -> dict[str, Any]:
    return {"id": tool_id, "name": name, "input": input_}


def test_tool_only_turn_becomes_tool_use_blocks_without_empty_text() -> None:
    out = _provider()._convert_messages([
        {"role": "user", "content": "read it"},
        {"role": "assistant", "content": "", "tool_uses": [_use("t1", path="a.py")]},
    ])
    assert out[1] == {
        "role":    "assistant",
        "content": [{"type": "tool_use", "id": "t1", "name": "FileRead", "input": {"path": "a.py"}}],
    }


def test_text_and_tool_calls_keep_text_first() -> None:
    out = _provider()._convert_messages([
        {"role": "assistant", "content": "looking", "tool_uses": [_use("t1"), _use("t2")]},
    ])
    kinds = [block["type"] for block in out[0]["content"]]
    assert kinds == ["text", "tool_use", "tool_use"]


def test_parallel_results_share_one_user_message_in_call_order() -> None:
    out = _provider()._convert_messages([
        {"role": "assistant", "content": "", "tool_uses": [_use("t1"), _use("t2")]},
        {"role": "tool", "content": "one", "tool_use_id": "t1"},
        {"role": "tool", "content": "two", "tool_use_id": "t2", "is_error": True},
    ])
    assert [m["role"] for m in out] == ["assistant", "user"]
    results = out[1]["content"]
    assert [(b["tool_use_id"], b["content"], b["is_error"]) for b in results] == [
        ("t1", "one", False),
        ("t2", "two", True),
    ]


def test_note_injected_after_results_joins_the_same_user_message_after_them() -> None:
    out = _provider()._convert_messages([
        {"role": "assistant", "content": "", "tool_uses": [_use("t1")]},
        {"role": "tool", "content": "ok", "tool_use_id": "t1"},
        {"role": "user", "content": "[Background task bg-1 completed] done"},
    ])
    assert len(out) == 2
    kinds = [block["type"] for block in out[1]["content"]]
    assert kinds == ["tool_result", "text"]


def test_consecutive_user_messages_are_merged() -> None:
    out = _provider()._convert_messages([
        {"role": "user", "content": "first"},
        {"role": "user", "content": "second"},
    ])
    assert len(out) == 1
    assert [b["text"] for b in out[0]["content"]] == ["first", "second"]


def test_blank_messages_are_dropped() -> None:
    out = _provider()._convert_messages([
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "   "},
        {"role": "user", "content": ""},
    ])
    assert [m["role"] for m in out] == ["user"]


def test_every_tool_result_has_a_matching_tool_use_before_it() -> None:
    out = _provider()._convert_messages([
        {"role": "user", "content": "go"},
        {"role": "assistant", "content": "", "tool_uses": [_use("t1")]},
        {"role": "tool", "content": "ok", "tool_use_id": "t1"},
        {"role": "assistant", "content": "done"},
    ])
    seen: set[str] = set()
    for message in out:
        for block in message["content"]:
            if block["type"] == "tool_use":
                seen.add(block["id"])
            if block["type"] == "tool_result":
                assert block["tool_use_id"] in seen


def test_blocks_another_provider_left_on_a_turn_are_not_sent() -> None:
    thinking  = {"type": "thinking", "thinking": "t", "signature": "s"}
    reasoning = {"type": "reasoning", "id": "rs_1", "summary": [], "encrypted_content": "enc"}
    out = _provider()._convert_messages([
        {"role": "assistant", "content": "hi", "provider_blocks": [reasoning, thinking]},
    ])
    assert out[0]["content"] == [thinking, {"type": "text", "text": "hi"}]
