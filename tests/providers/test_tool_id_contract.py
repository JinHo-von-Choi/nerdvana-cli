"""Contract tests for tool_use_id handling across providers and the agent loop.

The identifier format is ``call_<tool name>_<8 hex chars>``. Gemini returns no
identifier of its own, so the client mints one on both the streaming and the
non-streaming path, and a tool result must be able to name the function it
answers. These tests pin that round trip, plus the narrowed stream_options
fallback in the OpenAI-compatible provider.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from nerdvana_cli.providers.base import ProviderConfig, ProviderName
from nerdvana_cli.providers.gemini_provider import (
    GeminiProvider,
    make_tool_use_id,
    tool_name_from_id,
)
from nerdvana_cli.providers.openai_provider import OpenAIProvider

TOOL_NAME = "read_file"
UNDERSCORED_TOOL_NAME = "symbol_find_referencing_symbols"


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


def _text_part(text: str) -> SimpleNamespace:
    return SimpleNamespace(text=text, function_call=None)


def _call_part(name: str, args: dict[str, Any]) -> SimpleNamespace:
    return SimpleNamespace(text=None, function_call=SimpleNamespace(name=name, args=args))


def _chunk(*parts: SimpleNamespace) -> SimpleNamespace:
    return SimpleNamespace(
        candidates=[SimpleNamespace(content=SimpleNamespace(parts=list(parts)))]
    )


class FakeGeminiModels:
    """Stands in for ``client.aio.models``."""

    def __init__(self, chunks: list[SimpleNamespace] | None = None, response: Any = None):
        self.chunks   = chunks or []
        self.response = response
        self.calls: list[dict[str, Any]] = []

    async def generate_content_stream(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        chunks = self.chunks

        async def _iter() -> Any:
            for chunk in chunks:
                yield chunk

        return _iter()

    async def generate_content(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def _gemini_provider(models: FakeGeminiModels) -> GeminiProvider:
    provider = GeminiProvider(ProviderConfig(provider=ProviderName.GEMINI, model="gemini-2.0-flash"))
    provider._client = SimpleNamespace(aio=SimpleNamespace(models=models))  # type: ignore[assignment]
    return provider


class FakeTool:
    """Minimal duck type for the provider's tool declaration builder."""

    name             = TOOL_NAME
    description_text = "Read a file"
    input_schema     = {
        "type": "object",
        "properties": {"path": {"type": "string", "description": "File path"}},
        "required": ["path"],
    }


async def _collect(provider: GeminiProvider, messages: list[dict[str, Any]], tools: list[Any]) -> list[Any]:
    return [ev async for ev in provider.stream("sys", messages, tools)]


def _function_response_names(contents: list[dict[str, Any]]) -> list[str]:
    names = []
    for entry in contents:
        for part in entry["parts"]:
            if "functionResponse" in part:
                names.append(part["functionResponse"]["name"])
    return names


# ---------------------------------------------------------------------------
# Identifier format
# ---------------------------------------------------------------------------


class TestToolUseIdFormat:
    def test_generated_id_carries_name_and_nonce(self):
        tool_id = make_tool_use_id(TOOL_NAME)
        assert tool_id.startswith(f"call_{TOOL_NAME}_")
        assert len(tool_id.rsplit("_", 1)[1]) == 8

    def test_ids_are_unique_per_call(self):
        ids = {make_tool_use_id(TOOL_NAME) for _ in range(100)}
        assert len(ids) == 100

    def test_name_recovered_from_id(self):
        assert tool_name_from_id(make_tool_use_id(TOOL_NAME)) == TOOL_NAME

    def test_name_with_underscores_survives_round_trip(self):
        recovered = tool_name_from_id(make_tool_use_id(UNDERSCORED_TOOL_NAME))
        assert recovered == UNDERSCORED_TOOL_NAME

    def test_foreign_id_yields_no_name(self):
        assert tool_name_from_id("call_abc123") == ""
        assert tool_name_from_id("") == ""


# ---------------------------------------------------------------------------
# S8: Gemini streaming round trip
# ---------------------------------------------------------------------------


class TestGeminiStreamingToolRoundTrip:
    async def test_streamed_call_gets_an_id_and_the_result_names_the_function(self):
        models = FakeGeminiModels(chunks=[_chunk(_call_part(TOOL_NAME, {"path": "a.py"}))])
        provider = _gemini_provider(models)

        events = await _collect(provider, [{"role": "user", "content": "read a.py"}], [FakeTool()])
        tool_events = [ev for ev in events if ev.type == "tool_use_complete"]

        assert len(tool_events) == 1
        assert tool_events[0].tool_name == TOOL_NAME
        assert tool_events[0].tool_use_id, "streaming path must mint a tool_use_id"
        assert tool_events[0].tool_input_complete == {"path": "a.py"}

        tool_use_id = tool_events[0].tool_use_id
        contents = provider._convert_messages(
            [
                {"role": "user", "content": "read a.py"},
                {
                    "role": "assistant",
                    "content": "",
                    "tool_uses": [{"id": tool_use_id, "name": TOOL_NAME, "input": {"path": "a.py"}}],
                },
                {"role": "tool", "content": "file body", "tool_use_id": tool_use_id, "is_error": False},
            ]
        )

        assert _function_response_names(contents) == [TOOL_NAME]

    async def test_streamed_call_emits_matching_function_call_part(self):
        models = FakeGeminiModels(chunks=[_chunk(_call_part(TOOL_NAME, {"path": "a.py"}))])
        provider = _gemini_provider(models)
        events = await _collect(provider, [{"role": "user", "content": "go"}], [FakeTool()])
        tool_use_id = next(ev for ev in events if ev.type == "tool_use_complete").tool_use_id

        contents = provider._convert_messages(
            [
                {
                    "role": "assistant",
                    "content": "",
                    "tool_uses": [{"id": tool_use_id, "name": TOOL_NAME, "input": {"path": "a.py"}}],
                },
            ]
        )
        calls = [part["functionCall"] for part in contents[0]["parts"] if "functionCall" in part]
        assert calls == [{"name": TOOL_NAME, "args": {"path": "a.py"}}]

    async def test_text_and_done_events(self):
        models = FakeGeminiModels(chunks=[_chunk(_text_part("hello ")), _chunk(_text_part("world"))])
        provider = _gemini_provider(models)
        events = await _collect(provider, [{"role": "user", "content": "hi"}], [])

        assert "".join(ev.content for ev in events if ev.type == "content_delta") == "hello world"
        assert events[-1].type == "done"
        assert events[-1].stop_reason == "end_turn"

    async def test_stream_failure_becomes_error_event(self):
        class Boom(FakeGeminiModels):
            async def generate_content_stream(self, **kwargs: Any) -> Any:
                raise RuntimeError("upstream down")

        provider = _gemini_provider(Boom())
        events = await _collect(provider, [{"role": "user", "content": "hi"}], [])
        assert events[-1].type == "error"
        assert "upstream down" in events[-1].error


# ---------------------------------------------------------------------------
# S8: Gemini non-streaming round trip
# ---------------------------------------------------------------------------


class TestGeminiSendToolRoundTrip:
    async def test_send_call_id_resolves_back_to_the_function_name(self):
        response = SimpleNamespace(
            candidates=[SimpleNamespace(content=SimpleNamespace(parts=[_call_part(TOOL_NAME, {"path": "b.py"})]))],
            usage_metadata=SimpleNamespace(prompt_token_count=11, candidates_token_count=7),
        )
        provider = _gemini_provider(FakeGeminiModels(response=response))

        result = await provider.send("sys", [{"role": "user", "content": "read b.py"}], [FakeTool()])

        assert result["stop_reason"] == "tool_use"
        assert result["usage"] == {"input_tokens": 11, "output_tokens": 7}
        call = result["tool_uses"][0]
        assert call["name"] == TOOL_NAME
        assert call["input"] == {"path": "b.py"}

        contents = provider._convert_messages(
            [
                {
                    "role": "assistant",
                    "content": "",
                    "tool_uses": [{"id": call["id"], "name": call["name"], "input": call["input"]}],
                },
                {"role": "tool", "content": "file body", "tool_use_id": call["id"]},
            ]
        )
        assert _function_response_names(contents) == [TOOL_NAME]

    async def test_send_text_only(self):
        response = SimpleNamespace(
            candidates=[SimpleNamespace(content=SimpleNamespace(parts=[_text_part("done")]))],
            usage_metadata=None,
        )
        provider = _gemini_provider(FakeGeminiModels(response=response))
        result = await provider.send("sys", [{"role": "user", "content": "hi"}], [])

        assert result["content"] == "done"
        assert result["stop_reason"] == "end_turn"
        assert result["usage"] == {}

    async def test_send_failure_is_reported_as_error(self):
        provider = _gemini_provider(FakeGeminiModels(response=RuntimeError("quota exhausted")))
        result = await provider.send("sys", [{"role": "user", "content": "hi"}], [])
        assert result["is_error"] is True
        assert "quota exhausted" in result["content"]


# ---------------------------------------------------------------------------
# Name resolution fallbacks in message conversion
# ---------------------------------------------------------------------------


class TestGeminiNameResolution:
    def test_orphan_result_falls_back_to_the_id_format(self):
        provider = object.__new__(GeminiProvider)
        tool_id = make_tool_use_id(UNDERSCORED_TOOL_NAME)
        contents = provider._convert_messages([{"role": "tool", "content": "ok", "tool_use_id": tool_id}])
        assert _function_response_names(contents) == [UNDERSCORED_TOOL_NAME]

    def test_unresolvable_result_is_marked_unknown(self):
        provider = object.__new__(GeminiProvider)
        contents = provider._convert_messages([{"role": "tool", "content": "ok", "tool_use_id": "call_xyz"}])
        assert _function_response_names(contents) == ["unknown"]

    def test_explicit_name_on_the_result_wins(self):
        provider = object.__new__(GeminiProvider)
        contents = provider._convert_messages(
            [{"role": "tool", "content": "ok", "tool_use_id": make_tool_use_id("stale"), "tool_name": TOOL_NAME}]
        )
        assert _function_response_names(contents) == [TOOL_NAME]

    def test_inline_tool_use_content_blocks_are_mapped(self):
        provider = object.__new__(GeminiProvider)
        tool_id = "call_opaque_id_from_another_provider"
        contents = provider._convert_messages(
            [
                {
                    "role": "assistant",
                    "content": [
                        {"type": "text", "text": "thinking"},
                        {"type": "tool_use", "id": tool_id, "name": TOOL_NAME, "input": {"path": "c.py"}},
                    ],
                },
                {"role": "tool", "content": "body", "tool_use_id": tool_id},
            ]
        )
        assert _function_response_names(contents) == [TOOL_NAME]

    def test_user_tool_result_blocks_resolve_by_id(self):
        provider = object.__new__(GeminiProvider)
        tool_id = make_tool_use_id(TOOL_NAME)
        contents = provider._convert_messages(
            [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "here"},
                        {"type": "tool_result", "tool_use_id": tool_id, "content": "body"},
                    ],
                }
            ]
        )
        assert _function_response_names(contents) == [TOOL_NAME]

    def test_plain_roles_are_passed_through(self):
        provider = object.__new__(GeminiProvider)
        contents = provider._convert_messages(
            [
                {"role": "user", "content": "hi"},
                {"role": "assistant", "content": "hello"},
                {"role": "system", "content": "ignored role"},
                {"role": "assistant", "content": ""},
            ]
        )
        assert [entry["role"] for entry in contents] == ["user", "model", "user", "model"]
        assert contents[1]["parts"] == [{"text": "hello"}]
        assert contents[3]["parts"] == [{"text": ""}]


class TestGeminiListModels:
    async def test_list_models_strips_the_models_prefix(self):
        provider = GeminiProvider(ProviderConfig(provider=ProviderName.GEMINI))
        provider._client = SimpleNamespace(  # type: ignore[assignment]
            models=SimpleNamespace(
                list=lambda: [
                    SimpleNamespace(name="models/gemini-2.0-flash", display_name="Flash"),
                    SimpleNamespace(name="models/gemini-1.5-pro", display_name=None),
                ]
            )
        )
        models = await provider.list_models()
        assert [m.id for m in models] == ["gemini-1.5-pro", "gemini-2.0-flash"]
        assert models[1].name == "Flash"

    async def test_list_models_swallows_transport_failure(self):
        provider = GeminiProvider(ProviderConfig(provider=ProviderName.GEMINI))

        def _boom() -> Any:
            raise RuntimeError("no network")

        provider._client = SimpleNamespace(models=SimpleNamespace(list=_boom))  # type: ignore[assignment]
        assert await provider.list_models() == []


# ---------------------------------------------------------------------------
# C15: the session log records the real tool name
# ---------------------------------------------------------------------------


class RecordingSession:
    def __init__(self) -> None:
        self.tool_results: list[dict[str, Any]] = []

    def record_assistant_message(self, text: str, tool_uses: Any = None) -> None:
        return None

    def record_tool_result(self, tool_name: str, tool_use_id: str, content: str, is_error: bool = False) -> None:
        self.tool_results.append({"tool_name": tool_name, "tool_use_id": tool_use_id, "is_error": is_error})


class TestSessionLogToolName:
    async def test_session_log_names_the_executed_tool(self):
        from nerdvana_cli.core.agent_loop import AgentLoop
        from nerdvana_cli.types import SessionState, ToolResult

        first  = make_tool_use_id(TOOL_NAME)
        second = make_tool_use_id(UNDERSCORED_TOOL_NAME)
        tool_uses = [
            {"id": first, "name": TOOL_NAME, "input": {"path": "a.py"}},
            {"id": second, "name": UNDERSCORED_TOOL_NAME, "input": {}},
        ]

        class Executor:
            async def run_batch(self, calls: list[dict[str, Any]], ctx: Any) -> list[ToolResult]:
                # Results come back grouped by concurrency safety, not in call order.
                return [
                    ToolResult(tool_use_id=second, content="refs"),
                    ToolResult(tool_use_id=first, content="body"),
                ]

        session = RecordingSession()
        loop = SimpleNamespace(session=session, tool_executor=Executor(), state=SessionState())

        markers = [m async for m in AgentLoop._handle_tool_use_stop(loop, "", tool_uses, None)]

        logged = {entry["tool_use_id"]: entry["tool_name"] for entry in session.tool_results}
        assert logged == {first: TOOL_NAME, second: UNDERSCORED_TOOL_NAME}
        assert "unknown" not in logged.values()
        assert any(UNDERSCORED_TOOL_NAME in m for m in markers)


# ---------------------------------------------------------------------------
# S9: the stream_options fallback only retries an unsupported parameter
# ---------------------------------------------------------------------------


def _openai_error(cls: Any, status: int, message: str) -> Exception:
    request  = httpx.Request("POST", "https://api.example.test/v1/chat/completions")
    response = httpx.Response(status, request=request)
    return cls(message, response=response, body=None)


class CountingCompletions:
    def __init__(self, first_error: Exception | None, chunks: list[Any] | None = None):
        self.first_error = first_error
        self.chunks      = chunks or []
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if len(self.calls) == 1 and self.first_error is not None:
            raise self.first_error
        chunks = self.chunks

        async def _iter() -> Any:
            for chunk in chunks:
                yield chunk

        return _iter()


def _openai_provider(completions: CountingCompletions) -> OpenAIProvider:
    provider = OpenAIProvider(ProviderConfig(provider=ProviderName.OPENAI, model="gpt-4o-mini"))
    provider._client = SimpleNamespace(chat=SimpleNamespace(completions=completions))  # type: ignore[assignment]
    return provider


def _openai_chunks() -> list[SimpleNamespace]:
    delta_choice = SimpleNamespace(
        delta=SimpleNamespace(content="hello", tool_calls=None),
        finish_reason=None,
    )
    final_choice = SimpleNamespace(
        delta=SimpleNamespace(content=None, tool_calls=None),
        finish_reason="stop",
    )
    return [
        SimpleNamespace(choices=[delta_choice], usage=None),
        SimpleNamespace(choices=[final_choice], usage=None),
    ]


class TestStreamOptionsFallback:
    @pytest.mark.parametrize(
        ("cls_name", "status"),
        [("AuthenticationError", 401), ("RateLimitError", 429), ("InternalServerError", 500)],
    )
    async def test_billable_failures_are_not_retried(self, cls_name: str, status: int):
        import openai

        error = _openai_error(getattr(openai, cls_name), status, f"boom {status}")
        completions = CountingCompletions(first_error=error)
        provider = _openai_provider(completions)

        events = [ev async for ev in provider.stream("sys", [{"role": "user", "content": "hi"}], [])]

        assert len(completions.calls) == 1, "a non-parameter failure must not resend the request"
        assert events[-1].type == "error"
        assert f"boom {status}" in events[-1].error

    async def test_unsupported_stream_options_still_falls_back(self):
        import openai

        error = _openai_error(openai.BadRequestError, 400, "unknown parameter: stream_options")
        completions = CountingCompletions(first_error=error, chunks=_openai_chunks())
        provider = _openai_provider(completions)

        events = [ev async for ev in provider.stream("sys", [{"role": "user", "content": "hi"}], [])]

        assert len(completions.calls) == 2
        assert "stream_options" in completions.calls[0]
        assert "stream_options" not in completions.calls[1]
        assert "".join(ev.content for ev in events if ev.type == "content_delta") == "hello"
        assert any(ev.type == "done" and ev.stop_reason == "end_turn" for ev in events)

    async def test_client_side_rejection_falls_back(self):
        completions = CountingCompletions(
            first_error=TypeError("create() got an unexpected keyword argument 'stream_options'"),
            chunks=_openai_chunks(),
        )
        provider = _openai_provider(completions)

        events = [ev async for ev in provider.stream("sys", [{"role": "user", "content": "hi"}], [])]

        assert len(completions.calls) == 2
        assert any(ev.type == "done" for ev in events)

    async def test_supported_endpoint_calls_once(self):
        completions = CountingCompletions(first_error=None, chunks=_openai_chunks())
        provider = _openai_provider(completions)

        events = [ev async for ev in provider.stream("sys", [{"role": "user", "content": "hi"}], [])]

        assert len(completions.calls) == 1
        assert any(ev.type == "done" for ev in events)
