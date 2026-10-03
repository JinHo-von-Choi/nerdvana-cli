"""Anthropic server-side tool search: deferred MCP tools, the search tool, cache breakpoints and block round trips.

Payload shapes follow the tool search page of the Anthropic documentation; nothing here touches the network.

Author: 최진호
Date:   2026-10-03

"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any, ClassVar
from unittest.mock import AsyncMock

import anthropic
import pytest

try:
    import httpx2 as httpx  # the SDK's own HTTP layer from 1.0 on
except ImportError:          # pragma: no cover - older SDKs
    import httpx  # type: ignore[no-redef]

from nerdvana_cli.core.agent_loop import AgentLoop
from nerdvana_cli.core.config.settings import ModelConfig, NerdvanaSettings
from nerdvana_cli.core.state.session import SessionStorage, messages_from_transcript
from nerdvana_cli.core.tool import BaseTool, ToolRegistry
from nerdvana_cli.providers.anthropic_features import TOOL_SEARCH_TOOLS, declare_tools
from nerdvana_cli.providers.anthropic_provider import AnthropicProvider, with_cache_breakpoints
from nerdvana_cli.providers.base import ProviderConfig, ProviderName
from nerdvana_cli.providers.factory import create_provider
from nerdvana_cli.types import ToolResult

MODEL = "claude-sonnet-5-5"

SERVER_TOOL_USE = {"type": "server_tool_use", "id": "srvtoolu_01ABC123", "name": "tool_search_tool_regex", "input": {"pattern": "weather", "limit": 10}}
SEARCH_RESULT   = {
    "type": "tool_search_tool_result",
    "tool_use_id": "srvtoolu_01ABC123",
    "content": {"type": "tool_search_tool_search_result", "tool_references": [{"type": "tool_reference", "tool_name": "mcp__w__get_weather"}]},
}


class _Tool:
    def __init__(self, name: str, mcp: bool = False) -> None:
        self.name             = name
        self.description_text = f"{name} description"
        self.input_schema     = {"type": "object", "properties": {}}
        self.tags             = frozenset({"mcp"}) if mcp else frozenset()


BUILTIN = [_Tool("FileRead"), _Tool("Bash")]
MCP     = [_Tool("mcp__w__get_weather", mcp=True), _Tool("mcp__w__forecast", mcp=True)]


# ---------------------------------------------------------------------------
# Declarations
# ---------------------------------------------------------------------------


def test_off_declares_every_tool_in_full() -> None:
    declared = declare_tools([*BUILTIN, *MCP], MODEL, "off")
    assert [t["name"] for t in declared] == ["FileRead", "Bash", "mcp__w__get_weather", "mcp__w__forecast"]
    assert all("defer_loading" not in t and "type" not in t for t in declared)


@pytest.mark.parametrize("mode", ["bm25", "regex"])
def test_mcp_tools_are_deferred_behind_the_search_tool(mode: str) -> None:
    declared = declare_tools([*BUILTIN, *MCP], MODEL, mode)
    names    = [t["name"] for t in declared]
    assert names == ["FileRead", "Bash", f"tool_search_tool_{mode}", "mcp__w__get_weather", "mcp__w__forecast"]
    assert declared[2] == TOOL_SEARCH_TOOLS[mode]
    assert declared[2]["type"] == f"tool_search_tool_{mode}_20251119"
    assert [t.get("defer_loading") for t in declared] == [None, None, None, True, True]


def test_the_deferred_declarations_still_carry_their_full_definition() -> None:
    deferred = declare_tools([*BUILTIN, *MCP], MODEL, "bm25")[-1]
    assert deferred["description"] == "mcp__w__forecast description"
    assert deferred["input_schema"] == {"type": "object", "properties": {}}


def test_at_least_one_tool_stays_loaded_even_when_every_tool_is_an_mcp_tool() -> None:
    declared = declare_tools(MCP, MODEL, "regex")
    assert [t.get("defer_loading") for t in declared] == [None, True, True]
    assert declared[0]["name"] == "tool_search_tool_regex"


def test_without_mcp_tools_nothing_is_deferred_and_no_search_tool_is_added() -> None:
    declared = declare_tools(BUILTIN, MODEL, "bm25")
    assert [t["name"] for t in declared] == ["FileRead", "Bash"]


def test_an_anthropic_compatible_endpoint_with_another_model_is_left_alone() -> None:
    declared = declare_tools([*BUILTIN, *MCP], "MiniMax-M2", "bm25")
    assert all("defer_loading" not in t for t in declared)
    assert len(declared) == 4


def test_deferred_tools_carry_no_cache_control_and_the_last_loaded_tool_does() -> None:
    tools = declare_tools([*BUILTIN, *MCP], MODEL, "bm25")
    _, marked, _ = with_cache_breakpoints("sys", tools, [{"role": "user", "content": [{"type": "text", "text": "q"}]}])
    assert [t["name"] for t in marked if "cache_control" in t] == ["tool_search_tool_bm25"]
    assert all("cache_control" not in t for t in marked if t.get("defer_loading"))


def test_without_deferral_the_last_tool_still_carries_the_breakpoint() -> None:
    _, marked, _ = with_cache_breakpoints("sys", declare_tools(BUILTIN, MODEL, "off"), [])
    assert [t["name"] for t in marked if "cache_control" in t] == ["Bash"]


async def test_the_request_carries_the_declarations_the_setting_asks_for() -> None:
    provider = AnthropicProvider(ProviderConfig(provider=ProviderName.ANTHROPIC, api_key="k", model=MODEL, anthropic_tool_search="bm25"))
    create   = AsyncMock(side_effect=lambda **_: _Events([]))
    provider._client = SimpleNamespace(messages=SimpleNamespace(create=create))  # type: ignore[assignment]
    _ = [e async for e in provider.stream("sys", [{"role": "user", "content": "q"}], [*BUILTIN, *MCP])]
    tools = create.await_args.kwargs["tools"]
    assert [t["name"] for t in tools][2] == "tool_search_tool_bm25"
    assert [bool(t.get("defer_loading")) for t in tools] == [False, False, False, True, True]
    assert "anthropic-beta" not in create.await_args.kwargs.get("extra_headers", {})


def test_the_factory_and_the_settings_carry_the_option() -> None:
    assert ModelConfig().anthropic_tool_search == "off"
    assert ModelConfig(anthropic_tool_search="regex").anthropic_tool_search == "regex"
    with pytest.raises(ValueError):
        ModelConfig(anthropic_tool_search="vector")
    provider = create_provider(provider="anthropic", model=MODEL, api_key="k", anthropic_tool_search="regex")
    assert provider.config.anthropic_tool_search == "regex"  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# Streaming and conversion
# ---------------------------------------------------------------------------


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


def _search_stream() -> list[Any]:
    """The events of the documentation's streaming example, followed by a call to the discovered tool."""
    return [
        _ns(type="message_start", message=_ns(usage=_ns(input_tokens=10, output_tokens=1))),
        _ns(type="content_block_start", index=0, content_block=_ns(type="text", text="")),
        _ns(type="content_block_delta", index=0, delta=_ns(type="text_delta", text="Searching.")),
        _ns(type="content_block_stop", index=0),
        _ns(type="content_block_start", index=1, content_block=_ns(type="server_tool_use", id="srvtoolu_01ABC123", name="tool_search_tool_regex")),
        _ns(type="content_block_delta", index=1, delta=_ns(type="input_json_delta", partial_json='{"pattern":"wea')),
        _ns(type="content_block_delta", index=1, delta=_ns(type="input_json_delta", partial_json='ther","limit":10}')),
        _ns(type="content_block_stop", index=1),
        _ns(type="content_block_start", index=2, content_block=_ns(
            type="tool_search_tool_result", tool_use_id="srvtoolu_01ABC123",
            content=_ns(type="tool_search_tool_search_result", tool_references=[_ns(type="tool_reference", tool_name="mcp__w__get_weather")]),
        )),
        _ns(type="content_block_stop", index=2),
        _ns(type="content_block_start", index=3, content_block=_ns(type="tool_use", id="toolu_01", name="mcp__w__get_weather")),
        _ns(type="content_block_delta", index=3, delta=_ns(type="input_json_delta", partial_json='{"location":"SF"}')),
        _ns(type="content_block_stop", index=3),
        _ns(type="message_delta", usage=_ns(output_tokens=40), delta=_ns(stop_reason="tool_use")),
    ]


def _provider(events: list[Any]) -> AnthropicProvider:
    provider = AnthropicProvider(ProviderConfig(provider=ProviderName.ANTHROPIC, api_key="k", model=MODEL, anthropic_tool_search="regex"))
    provider._client = _ns(messages=_ns(create=AsyncMock(side_effect=lambda **_: _Events(events))))  # type: ignore[assignment]
    return provider


async def test_a_streamed_search_becomes_provider_blocks_and_only_the_client_tool_is_reported() -> None:
    events = [e async for e in _provider(_search_stream()).stream("sys", [{"role": "user", "content": "weather?"}], [])]
    assert [e.block for e in events if e.type == "provider_block"] == [SERVER_TOOL_USE, SEARCH_RESULT]
    assert [e.tool_name for e in events if e.type == "tool_use_start"] == ["mcp__w__get_weather"]
    completes = [e for e in events if e.type == "tool_use_complete"]
    assert [(e.tool_use_id, e.tool_input_complete) for e in completes] == [("toolu_01", {"location": "SF"})]
    assert "".join(e.tool_input_delta for e in events if e.type == "tool_use_delta") == '{"location":"SF"}'
    assert [e.stop_reason for e in events if e.type == "done"] == ["tool_use"]


def test_the_blocks_go_back_unchanged_before_the_text_and_the_tool_call() -> None:
    provider  = _provider([])
    converted = provider._convert_messages([
        {"role": "user", "content": "weather?"},
        {
            "role": "assistant", "content": "Searching.", "provider_blocks": [SERVER_TOOL_USE, SEARCH_RESULT],
            "tool_uses": [{"id": "toolu_01", "name": "mcp__w__get_weather", "input": {"location": "SF"}}],
        },
        {"role": "tool", "content": "sunny", "tool_use_id": "toolu_01"},
    ])
    assert [b["type"] for b in converted[1]["content"]] == ["server_tool_use", "tool_search_tool_result", "text", "tool_use"]
    assert converted[1]["content"][:2] == [SERVER_TOOL_USE, SEARCH_RESULT]
    assert [b["type"] for b in converted[2]["content"]] == ["tool_result"]
    assert converted[2]["content"][0]["tool_use_id"] == "toolu_01"


def _real_sdk(captured: list[httpx.Request], reply: dict[str, Any]) -> anthropic.AsyncAnthropic:
    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json=reply)

    return anthropic.AsyncAnthropic(api_key="k", http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))


async def test_the_real_sdk_response_blocks_survive_a_send_and_the_next_request() -> None:
    reply = {
        "id": "msg_1", "type": "message", "role": "assistant", "model": MODEL, "stop_reason": "tool_use", "stop_sequence": None,
        "content": [
            {"type": "text", "text": "I'll search."}, SERVER_TOOL_USE, SEARCH_RESULT,
            {"type": "tool_use", "id": "toolu_01", "name": "mcp__w__get_weather", "input": {"location": "SF"}},
        ],
        "usage": {"input_tokens": 10, "output_tokens": 20},
    }
    captured: list[httpx.Request] = []
    provider = _provider([])
    provider._client = _real_sdk(captured, reply)

    result = await provider.send("sys", [{"role": "user", "content": "weather?"}], [*BUILTIN, *MCP])
    assert result["provider_blocks"] == [SERVER_TOOL_USE, SEARCH_RESULT]
    assert result["tool_uses"] == [{"id": "toolu_01", "name": "mcp__w__get_weather", "input": {"location": "SF"}}]

    history = [
        {"role": "user", "content": "weather?"},
        {"role": "assistant", "content": result["content"], "tool_uses": result["tool_uses"], "provider_blocks": result["provider_blocks"]},
        {"role": "tool", "content": "sunny", "tool_use_id": "toolu_01"},
    ]
    await provider.send("sys", history, [*BUILTIN, *MCP])
    body = json.loads(captured[1].content)
    assert body["messages"][1]["content"][:3] == [SERVER_TOOL_USE, SEARCH_RESULT, {"type": "text", "text": "I'll search."}]
    assert [t["name"] for t in body["tools"]][2] == "tool_search_tool_regex"
    assert body["tools"][3]["defer_loading"] is True
    assert "cache_control" not in body["tools"][3]
    assert not any(b.get("tool_use_id", "").startswith("srvtoolu") for b in body["messages"][2]["content"])


# ---------------------------------------------------------------------------
# Through the agent loop and the session transcript
# ---------------------------------------------------------------------------


class _Weather(BaseTool[Any]):
    name             = "mcp__w__get_weather"
    description_text = "weather"
    tags: ClassVar[frozenset[str]] = frozenset({"mcp"})

    async def call(self, args: Any, context: Any, can_use_tool: Any = None, on_progress: Any = None) -> ToolResult:
        return ToolResult(tool_use_id="", content="sunny")


async def test_search_blocks_are_kept_by_the_loop_sent_back_and_restored_from_the_transcript(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    provider = _provider([])
    streams  = [_Events(_search_stream()), _Events([
        _ns(type="content_block_start", content_block=_ns(type="text", text="")),
        _ns(type="content_block_delta", delta=_ns(type="text_delta", text="Sunny.")),
        _ns(type="message_delta", usage=_ns(output_tokens=3), delta=_ns(stop_reason="end_turn")),
    ])]
    create = AsyncMock(side_effect=lambda **_: streams.pop(0))
    provider._client = _ns(messages=_ns(create=create))  # type: ignore[assignment]

    monkeypatch.setenv("NERDVANA_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setattr(AgentLoop, "create_provider_from_settings", lambda self: provider)
    monkeypatch.setattr(AgentLoop, "build_system_prompt", lambda self: "system")
    registry = ToolRegistry()
    registry.register(_Weather())
    settings     = NerdvanaSettings()
    settings.cwd = str(tmp_path)
    loop = AgentLoop(settings=settings, registry=registry, session=SessionStorage(session_id="t", storage_dir=str(tmp_path / "s")))

    async for _ in loop.run("weather?"):
        pass

    second = create.await_args_list[1].kwargs["messages"]
    assistant = next(m for m in second if m["role"] == "assistant")
    assert assistant["content"][:2] == [SERVER_TOOL_USE, SEARCH_RESULT]
    assert [b["type"] for b in assistant["content"]] == ["server_tool_use", "tool_search_tool_result", "text", "tool_use"]
    tool_results = [b for m in second if m["role"] == "user" and isinstance(m["content"], list) for b in m["content"] if b.get("type") == "tool_result"]
    assert [b["tool_use_id"] for b in tool_results] == ["toolu_01"]

    restored = messages_from_transcript(loop.session.replay())
    assert restored[1].provider_blocks == [SERVER_TOOL_USE, SEARCH_RESULT]
