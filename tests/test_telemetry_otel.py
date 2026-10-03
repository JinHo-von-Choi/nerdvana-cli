"""OpenTelemetry spans of a run, read back from the SDK's in-memory exporter.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("opentelemetry.sdk")

from opentelemetry.sdk.trace import ReadableSpan, TracerProvider  # noqa: E402
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExporter, SpanExportResult  # noqa: E402
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter  # noqa: E402
from opentelemetry.trace import StatusCode  # noqa: E402

from nerdvana_cli.core import telemetry_otel  # noqa: E402
from nerdvana_cli.core.agent_loop import AgentLoop  # noqa: E402
from nerdvana_cli.core.analytics import AnalyticsWriter, CallOrigin  # noqa: E402
from nerdvana_cli.core.hooks import HookContext, HookEvent  # noqa: E402
from nerdvana_cli.core.otel_semconv import Attr  # noqa: E402
from nerdvana_cli.core.secrets import MARKER  # noqa: E402
from nerdvana_cli.core.session import SessionStorage  # noqa: E402
from nerdvana_cli.core.settings import NerdvanaSettings
from nerdvana_cli.core.settings_sections import OtelConfig  # noqa: E402
from nerdvana_cli.core.tool import BaseTool, ToolRegistry  # noqa: E402
from nerdvana_cli.providers.base import ProviderEvent  # noqa: E402
from nerdvana_cli.tools.bash_tool import _build_env  # noqa: E402
from nerdvana_cli.types import ToolResult  # noqa: E402

GITHUB_TOKEN = "ghp_" + "a1B2c3D4e5" * 4
SCHEMA       = {"type": "object", "properties": {"note": {"type": "string"}}}


class _Echo(BaseTool[Any]):
    name             = "Echo"
    description_text = "echo"
    input_schema     = SCHEMA

    async def call(self, args: Any, context: Any, can_use_tool: Any = None, on_progress: Any = None) -> ToolResult:
        return ToolResult(tool_use_id="", content=f"echo {args.get('note', '')}")


class _Failing(_Echo):
    name = "Failing"

    async def call(self, args: Any, context: Any, can_use_tool: Any = None, on_progress: Any = None) -> ToolResult:
        return ToolResult(tool_use_id="", content="it broke", is_error=True)


class _TwoSteps:
    """One call of *tool*, then a final answer; each request reports usage with cache figures."""

    def __init__(self, tool: str = "Echo", note: str = "") -> None:
        self.calls = 0
        self.tool  = tool
        self.note  = note

    async def stream(self, system_prompt: str, messages: Any, tools: Any) -> AsyncIterator[ProviderEvent]:
        self.calls += 1
        if self.calls == 1:
            yield ProviderEvent(type="tool_use_complete", tool_use_id="c1", tool_name=self.tool, tool_input_complete={"note": self.note})
            yield ProviderEvent(type="usage", usage={"input_tokens": 100, "output_tokens": 5, "cache_read_tokens": 60, "cache_write_tokens": 30})
            yield ProviderEvent(type="done", stop_reason="tool_use")
        else:
            yield ProviderEvent(type="content_delta", content="done")
            yield ProviderEvent(type="usage", usage={"input_tokens": 150, "output_tokens": 7})
            yield ProviderEvent(type="done", stop_reason="end_turn")


class _Tracing:
    """Switches tracing on against an in-memory exporter and builds loops that report to it."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        self.monkeypatch = monkeypatch
        self.tmp_path    = tmp_path
        self.exporter    = InMemorySpanExporter()

    def start(self, capture: bool = False, exporter: SpanExporter | None = None) -> None:
        provider = TracerProvider()
        provider.add_span_processor(SimpleSpanProcessor(exporter or self.exporter))
        telemetry_otel.activate(provider, OtelConfig(enabled=True, capture_content=capture))

    def loop(self, session_id: str = "sess-1", origin: CallOrigin | None = None, provider: Any = None, tool: type[_Echo] = _Echo) -> AgentLoop:
        self.monkeypatch.setenv("NERDVANA_DATA_HOME", str(self.tmp_path / "data"))
        self.monkeypatch.setattr(AgentLoop, "create_provider_from_settings", lambda self: provider or _TwoSteps())
        self.monkeypatch.setattr(AgentLoop, "build_system_prompt", lambda self: "system")
        registry = ToolRegistry()
        registry.register(tool())
        settings                = NerdvanaSettings()
        settings.cwd            = str(self.tmp_path)
        settings.model.provider = "gemini"
        settings.model.model    = "model-x"
        return AgentLoop(
            settings         = settings,
            registry         = registry,
            session          = SessionStorage(session_id=session_id, storage_dir=str(self.tmp_path / "sessions")),
            analytics_writer = AnalyticsWriter(db_path=self.tmp_path / f"{session_id}.sqlite", enabled=False),
            origin           = origin,
        )

    @property
    def spans(self) -> list[ReadableSpan]:
        return list(self.exporter.get_finished_spans())

    def named(self, prefix: str) -> list[ReadableSpan]:
        return [span for span in self.spans if span.name.startswith(prefix)]

    def one(self, prefix: str) -> ReadableSpan:
        found = self.named(prefix)
        assert len(found) == 1, [span.name for span in self.spans]
        return found[0]


@pytest.fixture
def tracing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[_Tracing]:
    harness = _Tracing(monkeypatch, tmp_path)
    yield harness
    telemetry_otel.deactivate()


async def _drain(loop: AgentLoop, prompt: str = "go") -> str:
    out = ""
    async for chunk in loop.run(prompt):
        out += chunk
    return out


def _parent_id(span: ReadableSpan) -> int | None:
    return span.parent.span_id if span.parent else None


def _attrs(span: ReadableSpan) -> dict[str, Any]:
    return dict(span.attributes or {})


# ---------------------------------------------------------------------------
# Spans of a run
# ---------------------------------------------------------------------------


async def test_a_run_makes_an_agent_span_with_chat_and_tool_spans_below_it(tracing: _Tracing) -> None:
    tracing.start()
    await _drain(tracing.loop())
    run   = tracing.one("invoke_agent")
    chats = tracing.named("chat")
    tool  = tracing.one("execute_tool")
    assert (run.name, tool.name, [chat.name for chat in chats]) == ("invoke_agent main", "execute_tool Echo", ["chat model-x", "chat model-x"])
    assert run.parent is None
    assert {_parent_id(span) for span in (*chats, tool)} == {run.context.span_id}
    assert len({span.context.trace_id for span in tracing.spans}) == 1


async def test_the_chat_span_carries_the_request_and_its_token_usage(tracing: _Tracing) -> None:
    tracing.start()
    await _drain(tracing.loop())
    first, second = sorted(tracing.named("chat"), key=lambda span: span.start_time or 0)
    assert _attrs(first) == {
        Attr.OPERATION_NAME:      "chat",
        Attr.PROVIDER_NAME:       "gcp.gemini",
        Attr.REQUEST_MODEL:       "model-x",
        Attr.CONVERSATION_ID:     "sess-1",
        Attr.USAGE_INPUT_TOKENS:  100,
        Attr.USAGE_OUTPUT_TOKENS: 5,
        Attr.USAGE_CACHE_READ:    60,
        Attr.USAGE_CACHE_WRITE:   30,
        Attr.COST_USD:            0.0,
        Attr.TURN:                1,
    }
    assert (_attrs(second)[Attr.USAGE_INPUT_TOKENS], _attrs(second)[Attr.USAGE_CACHE_READ], _attrs(second)[Attr.TURN]) == (150, 0, 2)
    assert first.kind.name == "CLIENT" and first.start_time < first.end_time <= second.start_time


async def test_the_tool_span_carries_the_tool_and_the_call_id(tracing: _Tracing) -> None:
    tracing.start()
    await _drain(tracing.loop())
    tool = tracing.one("execute_tool")
    assert _attrs(tool) == {
        Attr.OPERATION_NAME:  "execute_tool",
        Attr.TOOL_NAME:       "Echo",
        Attr.TOOL_TYPE:       "function",
        Attr.CONVERSATION_ID: "sess-1",
        Attr.TOOL_CALL_ID:    "c1",
    }
    assert tool.kind.name == "INTERNAL" and tool.status.status_code is not StatusCode.ERROR


async def test_the_agent_span_names_the_agent_and_the_model(tracing: _Tracing) -> None:
    tracing.start()
    await _drain(tracing.loop())
    run = tracing.one("invoke_agent")
    assert _attrs(run) == {
        Attr.OPERATION_NAME:  "invoke_agent",
        Attr.AGENT_NAME:      "main",
        Attr.AGENT_ID:        "main",
        Attr.PROVIDER_NAME:   "gcp.gemini",
        Attr.REQUEST_MODEL:   "model-x",
        Attr.CONVERSATION_ID: "sess-1",
    }
    assert run.kind.name == "INTERNAL"


async def test_a_failed_tool_call_is_marked_with_error_type(tracing: _Tracing) -> None:
    tracing.start()
    await _drain(tracing.loop(provider=_TwoSteps(tool="Failing"), tool=_Failing))
    tool = tracing.one("execute_tool")
    assert _attrs(tool)[Attr.ERROR_TYPE] == "tool_error"
    assert tool.status.status_code is StatusCode.ERROR


async def test_each_prompt_is_its_own_agent_span(tracing: _Tracing) -> None:
    tracing.start()
    loop = tracing.loop()
    await _drain(loop, "first")
    loop.provider = _TwoSteps()
    await _drain(loop, "second")
    runs = sorted(tracing.named("invoke_agent"), key=lambda span: span.start_time or 0)
    assert len(runs) == 2 and runs[0].end_time <= runs[1].start_time
    assert {_parent_id(chat) for chat in tracing.named("chat")} == {run.context.span_id for run in runs}


async def test_closing_the_session_ends_nothing_twice(tracing: _Tracing) -> None:
    tracing.start()
    loop = tracing.loop()
    await _drain(loop)
    loop.close_session("exit")
    assert len(tracing.named("invoke_agent")) == 1


# ---------------------------------------------------------------------------
# Hook events that do not come in a clean pair
# ---------------------------------------------------------------------------


def _fire(loop: AgentLoop, event: HookEvent, **fields: Any) -> None:
    loop.hooks.fire(HookContext(event=event, settings=loop.settings, **fields))


def _api_call(loop: AgentLoop) -> None:
    _fire(loop, HookEvent.BEFORE_API_CALL, extra={"agent_loop": loop})


def test_a_call_refused_before_it_ran_shows_as_an_instant_error_span(tracing: _Tracing) -> None:
    tracing.start()
    loop = tracing.loop()
    _api_call(loop)
    _fire(loop, HookEvent.AFTER_TOOL, tool_name="Bash", tool_input={"command": "x"}, tool_result=ToolResult(tool_use_id="c9", content="refused", is_error=True))
    tool = tracing.one("execute_tool")
    assert (_attrs(tool)[Attr.TOOL_CALL_ID], _attrs(tool)[Attr.ERROR_TYPE], tool.start_time) == ("c9", "tool_error", tool.end_time)


def test_results_that_arrive_out_of_order_close_the_right_spans(tracing: _Tracing) -> None:
    tracing.start(capture=True)
    loop = tracing.loop()
    _api_call(loop)
    for note in ("a", "b"):
        _fire(loop, HookEvent.BEFORE_TOOL, tool_name="Echo", tool_input={"note": note})
    for note in ("b", "a"):
        _fire(loop, HookEvent.AFTER_TOOL, tool_name="Echo", tool_input={"note": note}, tool_result=ToolResult(tool_use_id=f"id-{note}", content=f"r-{note}"))
    pairs = {_attrs(span)[Attr.TOOL_CALL_ID]: (_attrs(span)[Attr.TOOL_CALL_ARGUMENTS], _attrs(span)[Attr.TOOL_CALL_RESULT]) for span in tracing.named("execute_tool")}
    assert pairs == {"id-a": ('{"note": "a"}', "r-a"), "id-b": ('{"note": "b"}', "r-b")}


def test_a_new_prompt_closes_what_the_last_one_left_open_at_its_last_event(tracing: _Tracing) -> None:
    tracing.start()
    loop = tracing.loop()
    _api_call(loop)
    _fire(loop, HookEvent.BEFORE_TOOL, tool_name="Echo", tool_input={})
    loop._turn += 1
    _api_call(loop)
    first_run = tracing.one("invoke_agent")
    tool      = tracing.one("execute_tool")
    assert _attrs(tool)[Attr.ERROR_TYPE] == "aborted"
    assert first_run.end_time == tool.end_time
    _fire(loop, HookEvent.SESSION_END, extra={"reason": "exit"})
    assert len(tracing.named("invoke_agent")) == 2


# ---------------------------------------------------------------------------
# Sub-agents
# ---------------------------------------------------------------------------


async def test_a_sub_agent_hangs_below_the_agent_call_of_its_parent(tracing: _Tracing) -> None:
    tracing.start()
    parent = tracing.loop("parent")
    _api_call(parent)
    _fire(parent, HookEvent.BEFORE_TOOL, tool_name="Agent", tool_input={"prompt": "p"})
    child = tracing.loop("child", origin=CallOrigin(agent_id="agent_1", agent_type="Explore", parent_session_id="parent"))
    await _drain(child)
    _fire(parent, HookEvent.AFTER_TOOL, tool_name="Agent", tool_input={"prompt": "p"}, tool_result=ToolResult(tool_use_id="a1", content="ok"))
    _fire(parent, HookEvent.SESSION_END, extra={"reason": "exit"})
    agent_call = tracing.one("execute_tool Agent")
    child_run  = tracing.one("invoke_agent Explore")
    assert _parent_id(child_run) == agent_call.context.span_id
    assert child_run.context.trace_id == agent_call.context.trace_id
    assert _attrs(child_run)[Attr.AGENT_ID] == "agent_1"
    child_spans = [span for span in tracing.spans if _attrs(span).get(Attr.CONVERSATION_ID) == "child" and span is not child_run]
    assert child_spans and {_parent_id(span) for span in child_spans} == {child_run.context.span_id}


async def test_a_sub_agent_whose_parent_is_not_traced_starts_its_own_trace(tracing: _Tracing) -> None:
    tracing.start()
    await _drain(tracing.loop("child", origin=CallOrigin(agent_type="Explore", parent_session_id="nobody")))
    assert tracing.one("invoke_agent Explore").parent is None


# ---------------------------------------------------------------------------
# Content capture
# ---------------------------------------------------------------------------


async def test_content_is_not_recorded_unless_asked_for(tracing: _Tracing) -> None:
    tracing.start()
    await _drain(tracing.loop(provider=_TwoSteps(note="private note")), "private prompt")
    keys = {key for span in tracing.spans for key in _attrs(span)}
    assert not keys & {Attr.INPUT_MESSAGES, Attr.TOOL_CALL_ARGUMENTS, Attr.TOOL_CALL_RESULT}
    assert "private" not in " ".join(str(value) for span in tracing.spans for value in _attrs(span).values())


async def test_captured_content_is_recorded_and_secrets_are_masked(tracing: _Tracing, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEPLOY_PASSWORD", "hunter2-hunter2")
    tracing.start(capture=True)
    await _drain(tracing.loop(provider=_TwoSteps(note=f"token {GITHUB_TOKEN}")), f"deploy with hunter2-hunter2 and {GITHUB_TOKEN}")
    tool      = tracing.one("execute_tool")
    messages  = _attrs(sorted(tracing.named("chat"), key=lambda span: span.start_time or 0)[1])[Attr.INPUT_MESSAGES]
    everything = " ".join(str(value) for span in tracing.spans for value in _attrs(span).values())
    assert "deploy with" in messages and "echo token" in _attrs(tool)[Attr.TOOL_CALL_RESULT]
    assert GITHUB_TOKEN not in everything and "hunter2-hunter2" not in everything
    assert MARKER in messages and MARKER in _attrs(tool)[Attr.TOOL_CALL_ARGUMENTS] and MARKER in _attrs(tool)[Attr.TOOL_CALL_RESULT]


async def test_the_history_is_recorded_as_roles_and_typed_parts(tracing: _Tracing) -> None:
    import json

    tracing.start(capture=True)
    await _drain(tracing.loop(provider=_TwoSteps(note="n")), "hello")
    second  = sorted(tracing.named("chat"), key=lambda span: span.start_time or 0)[1]
    history = json.loads(_attrs(second)[Attr.INPUT_MESSAGES])
    assert {"role": "user", "parts": [{"type": "text", "content": "hello"}]} in history
    assert {"type": "tool_call", "id": "c1", "name": "Echo", "arguments": {"note": "n"}} in [part for message in history for part in message["parts"]]
    assert any(part["type"] == "tool_call_response" and part["id"] == "c1" for message in history for part in message["parts"])


def test_long_content_is_cut_after_masking(tracing: _Tracing) -> None:
    tracing.start(capture=True)
    runtime = telemetry_otel._RUNTIME
    assert runtime is not None
    long = "x" * (telemetry_otel.MAX_CONTENT_CHARS + 500)
    cut  = runtime.text(long)
    assert len(cut) == telemetry_otel.MAX_CONTENT_CHARS + len("...[truncated]") and cut.endswith("...[truncated]")
    assert runtime.text(f"a {GITHUB_TOKEN} b") == f"a {MARKER}:github-token b"


# ---------------------------------------------------------------------------
# Trace context for child processes
# ---------------------------------------------------------------------------


def test_a_command_receives_the_trace_context_of_its_tool_call(tracing: _Tracing, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TRACEPARENT", raising=False)
    tracing.start()
    loop = tracing.loop()
    _api_call(loop)
    assert "TRACEPARENT" not in _build_env(str(tracing.tmp_path))
    _fire(loop, HookEvent.BEFORE_TOOL, tool_name="Bash", tool_input={"command": "env"})
    env = _build_env(str(tracing.tmp_path))
    _fire(loop, HookEvent.AFTER_TOOL, tool_name="Bash", tool_input={"command": "env"}, tool_result=ToolResult(tool_use_id="b1", content=""))
    version, trace_id, span_id, _flags = env["TRACEPARENT"].split("-")
    tool = tracing.one("execute_tool Bash")
    assert (version, int(trace_id, 16), int(span_id, 16)) == ("00", tool.context.trace_id, tool.context.span_id)
    assert "TRACEPARENT" not in _build_env(str(tracing.tmp_path))


def test_a_call_that_never_reported_back_leaves_no_trace_context_behind(tracing: _Tracing, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TRACEPARENT", raising=False)
    tracing.start()
    loop = tracing.loop()
    _api_call(loop)
    _fire(loop, HookEvent.BEFORE_TOOL, tool_name="Bash", tool_input={"command": "sleep 99"})
    assert telemetry_otel.trace_environment()
    _fire(loop, HookEvent.SESSION_END, extra={"reason": "exit"})
    assert telemetry_otel.trace_environment() == {}


# ---------------------------------------------------------------------------
# A broken exporter or handler never reaches the run
# ---------------------------------------------------------------------------


class _BrokenExporter(SpanExporter):
    def export(self, spans: Any) -> SpanExportResult:
        raise ConnectionError("collector is down")

    def shutdown(self) -> None:
        pass


async def test_an_exporter_that_fails_does_not_break_the_run(tracing: _Tracing) -> None:
    tracing.start(exporter=_BrokenExporter())
    output = await _drain(tracing.loop())
    assert "done" in output


async def test_a_handler_that_fails_does_not_break_the_run(tracing: _Tracing, monkeypatch: pytest.MonkeyPatch) -> None:
    tracing.start()

    def boom(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("tracer bug")

    monkeypatch.setattr(telemetry_otel, "_start", boom)
    output = await _drain(tracing.loop())
    assert "done" in output and tracing.spans == []


async def test_a_usage_report_the_observer_cannot_read_does_not_raise(tracing: _Tracing) -> None:
    tracing.start()
    loop = tracing.loop()
    assert loop.usage_listener is not None
    loop.usage_listener({"input_tokens": "not a number"})
    assert tracing.spans == []


# ---------------------------------------------------------------------------
# Switching on
# ---------------------------------------------------------------------------


def test_the_exporter_gets_the_traces_url_of_the_configured_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    import opentelemetry.exporter.otlp.proto.http.trace_exporter as exporter_module

    seen: list[str | None] = []

    class _Recorder(InMemorySpanExporter):
        def __init__(self, endpoint: str | None = None) -> None:
            super().__init__()
            seen.append(endpoint)

    monkeypatch.setattr(exporter_module, "OTLPSpanExporter", _Recorder)
    for endpoint in ("http://collector:4318", "http://collector:4318/", "http://collector:4318/v1/traces", ""):
        telemetry_otel._build_provider(OtelConfig(enabled=True, endpoint=endpoint)).shutdown()
    assert seen == ["http://collector:4318/v1/traces"] * 3 + [None]


def test_the_resource_names_the_service(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = telemetry_otel._build_provider(OtelConfig(enabled=True, service_name="my-agent"))
    try:
        assert provider.resource.attributes["service.name"] == "my-agent"
    finally:
        provider.shutdown()


def test_setup_turns_tracing_on_only_when_enabled() -> None:
    settings = NerdvanaSettings()
    assert telemetry_otel.setup(settings) == "" and not telemetry_otel.is_active()
    settings.telemetry.otel.enabled = True
    try:
        assert telemetry_otel.setup(settings) == "" and telemetry_otel.is_active()
    finally:
        telemetry_otel.deactivate()
    assert not telemetry_otel.is_active()


def test_a_setup_that_fails_for_another_reason_reports_it_and_stays_off(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(config: OtelConfig) -> Any:
        raise ValueError("bad endpoint")

    monkeypatch.setattr(telemetry_otel, "_build_provider", broken)
    settings = NerdvanaSettings()
    settings.telemetry.otel.enabled = True
    assert "ValueError: bad endpoint" in telemetry_otel.setup(settings) and not telemetry_otel.is_active()
