"""Gemini thought signatures: kept from a response and sent back with the function call.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

import base64
from types import SimpleNamespace
from typing import Any

import pytest

from nerdvana_cli.core.loop_state import LoopTurn
from nerdvana_cli.providers.base import ProviderConfig, ProviderName
from nerdvana_cli.providers.gemini_provider import GeminiProvider

SIGNATURE = b"\x00sig\xffbytes"
ENCODED   = base64.b64encode(SIGNATURE).decode("ascii")


def _provider(model: str = "gemini-3.1-pro-preview") -> GeminiProvider:
    return GeminiProvider(ProviderConfig(provider=ProviderName.GEMINI, model=model))


def _call_part(name: str, signature: bytes | None) -> SimpleNamespace:
    return SimpleNamespace(text=None, function_call=SimpleNamespace(name=name, args={"p": 1}), thought_signature=signature)


class _Models:
    def __init__(self, chunks: list[Any]) -> None:
        self.chunks = chunks

    async def generate_content_stream(self, **kwargs: Any) -> Any:
        async def _iter() -> Any:
            for chunk in self.chunks:
                yield chunk
        return _iter()


async def test_a_streamed_call_carries_its_signature_as_base64_text() -> None:
    provider = _provider()
    chunk    = SimpleNamespace(candidates=[SimpleNamespace(content=SimpleNamespace(parts=[_call_part("Read", SIGNATURE)]))], usage_metadata=None)
    provider._client = SimpleNamespace(aio=SimpleNamespace(models=_Models([chunk])))  # type: ignore[assignment]
    events = [e async for e in provider.stream("s", [{"role": "user", "content": "go"}], [])]
    (call,) = [e for e in events if e.type == "tool_use_complete"]
    assert call.tool_signature == ENCODED


def test_a_call_without_a_signature_reports_none() -> None:
    from nerdvana_cli.providers.gemini_provider import _read_call

    assert _read_call(_call_part("Read", None)) == ("Read", {"p": 1}, "")


def test_the_loop_keeps_the_signature_on_the_stored_call() -> None:
    turn = LoopTurn(messages=[], used_ids=set(), sent_count=0)
    turn.add_call("c1", "Read", {"p": 1}, ENCODED)
    turn.add_call("c2", "Grep", {}, "")
    assert turn.tool_uses[0]["thought_signature"] == ENCODED
    assert "thought_signature" not in turn.tool_uses[1]


def _history(first: dict[str, Any], second: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    uses = [first] + ([second] if second else [])
    return [
        {"role": "user", "content": "go"},
        {"role": "assistant", "content": "", "tool_uses": uses},
        *({"role": "tool", "tool_use_id": u["id"], "content": "ok"} for u in uses),
    ]


def test_the_stored_signature_is_sent_back_as_bytes_on_its_call() -> None:
    contents = _provider()._convert_messages(_history({"id": "c1", "name": "Read", "input": {}, "thought_signature": ENCODED}))
    assert contents[1]["parts"][0]["thoughtSignature"] == SIGNATURE


def test_a_gemini_3_call_without_a_signature_gets_the_documented_stand_in_on_the_first_call_only() -> None:
    contents = _provider()._convert_messages(_history(
        {"id": "c1", "name": "Read", "input": {}}, {"id": "c2", "name": "Grep", "input": {}},
    ))
    parts = contents[1]["parts"]
    assert parts[0]["thoughtSignature"] == b"skip_thought_signature_validator"
    assert "thoughtSignature" not in parts[1]


def test_older_models_receive_no_stand_in() -> None:
    contents = _provider("gemini-2.5-flash")._convert_messages(_history({"id": "c1", "name": "Read", "input": {}}))
    assert "thoughtSignature" not in contents[1]["parts"][0]


@pytest.mark.parametrize("bad", ["not base64!!", 5, None])
def test_an_unreadable_stored_signature_is_treated_as_absent(bad: Any) -> None:
    contents = _provider("gemini-2.5-flash")._convert_messages(_history({"id": "c1", "name": "Read", "input": {}, "thought_signature": bad}))
    assert "thoughtSignature" not in contents[1]["parts"][0]


def test_results_of_parallel_calls_share_one_user_message() -> None:
    contents = _provider()._convert_messages(_history(
        {"id": "c1", "name": "Read", "input": {}}, {"id": "c2", "name": "Grep", "input": {}},
    ))
    assert [c["role"] for c in contents] == ["user", "model", "user"]
    assert len(contents[2]["parts"]) == 2


def test_the_sdk_accepts_the_converted_parts() -> None:
    from google.genai import types

    contents = _provider()._convert_messages(_history({"id": "c1", "name": "Read", "input": {}, "thought_signature": ENCODED}))
    part     = types.Content.model_validate(contents[1]).parts[0]  # type: ignore[index]
    assert part.thought_signature == SIGNATURE
