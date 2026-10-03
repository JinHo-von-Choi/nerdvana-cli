"""OpenTelemetry traces of agent runs, model requests and tool calls.

Author: 최진호
Date:   2026-10-03

Tracing is optional and off by default. It needs the ``otel`` extra (the OpenTelemetry SDK and the
OTLP/HTTP exporter) and ``telemetry.otel.enabled``. Without the SDK the feature stays off and
:func:`setup` returns a one-line notice to show. When it is off nothing here imports OpenTelemetry.

The composition root calls :func:`setup` once after the settings are loaded. Every ``AgentLoop``,
sub-agents included, then calls :func:`observe_loop` while it is built (from
``register_activity_hooks``), which attaches a :class:`LoopObserver` to the loop's hook engine and
usage listener. No other part of the agent knows about tracing.

Spans follow the OpenTelemetry GenAI conventions, with every attribute name kept in
``core.otel_semconv``:

* ``invoke_agent {agent}`` (INTERNAL): one per run of a prompt. A sub-agent's span is a child of
  the open ``Agent`` or ``Swarm`` tool call of the session that started it.
* ``chat {model}`` (CLIENT): one per provider request, with the token usage and the cost.
* ``execute_tool {tool}`` (INTERNAL): one per tool call that passed the permission check; a call
  that was refused before running shows as an instantly closed span marked as an error.

The conversation, tool arguments and tool results are recorded only with
``telemetry.otel.capture_content``, and then always after :class:`SecretMasker`, cut to
``MAX_CONTENT_CHARS``. A failing exporter or a bug in a handler never reaches the agent: every
entry point catches its own errors.
"""

from __future__ import annotations

import contextvars
import json
import logging
import time
import weakref
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from nerdvana_cli import __version__
from nerdvana_cli.core import otel_semconv as sc
from nerdvana_cli.core.hooks import HookContext, HookEvent, HookResult
from nerdvana_cli.core.otel_semconv import Attr, Operation
from nerdvana_cli.core.secrets import SecretMasker

if TYPE_CHECKING:
    from nerdvana_cli.core.agent_loop import AgentLoop
    from nerdvana_cli.core.settings_sections import OtelConfig

logger = logging.getLogger(__name__)

SDK_MISSING_NOTICE = (
    "OpenTelemetry tracing is enabled (telemetry.otel.enabled) but the SDK is not installed, so it stays off. "
    "Install it with: pip install 'nerdvana-cli[otel]'"
)
MAX_CONTENT_CHARS = 32_768
_TRUNCATED        = "...[truncated]"
_TRACES_PATH      = "/v1/traces"

UsageListener = Callable[[dict[str, Any]], None]


@dataclass
class _Runtime:
    """What the observers share: the tracer, the content policy and the sessions currently traced."""

    tracer:          Any
    provider:        Any
    capture_content: bool
    masker:          SecretMasker
    observers:       weakref.WeakValueDictionary[str, LoopObserver]

    def text(self, raw: str) -> str:
        """*raw* with secret values replaced, cut to ``MAX_CONTENT_CHARS``."""
        masked = self.masker.mask(raw).text
        return masked if len(masked) <= MAX_CONTENT_CHARS else masked[:MAX_CONTENT_CHARS] + _TRUNCATED


@dataclass
class _OpenTool:
    """A tool call whose span is open: AFTER_TOOL finds it again by the call's name and input."""

    key:  tuple[str, str]
    name: str
    span: Any


_RUNTIME: _Runtime | None = None
_ACTIVE_TOOL_SPAN: contextvars.ContextVar[Any] = contextvars.ContextVar("nerdvana_otel_tool_span", default=None)


# ---------------------------------------------------------------------------
# Switching tracing on and off
# ---------------------------------------------------------------------------


def setup(settings: Any) -> str:
    """Turn tracing on when ``telemetry.otel.enabled`` asks for it. Returns a notice to show, or "".

    Safe to call more than once. The notice says why tracing stays off when it was asked for
    but cannot start (SDK not installed, exporter misconfigured).
    """
    config: OtelConfig = settings.telemetry.otel
    if not config.enabled or _RUNTIME is not None:
        return ""
    try:
        provider = _build_provider(config)
        activate(provider, config, _masker_for(settings))
    except ImportError:
        return SDK_MISSING_NOTICE
    except Exception as exc:  # noqa: BLE001
        logger.warning("OpenTelemetry setup failed", exc_info=True)
        return f"OpenTelemetry tracing stays off: {type(exc).__name__}: {exc}"
    return ""


def _build_provider(config: OtelConfig) -> Any:
    """A tracer provider that exports in batches to the OTLP/HTTP endpoint. Raises ImportError without the SDK."""
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    endpoint = config.endpoint.rstrip("/")
    if endpoint and not endpoint.endswith(_TRACES_PATH):
        endpoint += _TRACES_PATH
    provider = TracerProvider(resource=Resource.create({"service.name": config.service_name, "service.version": __version__}))
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint or None)))
    return provider


def _masker_for(settings: Any) -> SecretMasker:
    """The masker for captured content: the process's credential variables, the API key and the configured patterns."""
    key      = getattr(getattr(settings, "model", None), "api_key", "") or ""
    patterns = getattr(getattr(settings, "session", None), "mask_extra_patterns", ()) or ()
    return SecretMasker.from_environment({"API_KEY": key} if key else None, patterns)


def activate(provider: Any, config: OtelConfig, masker: SecretMasker | None = None) -> None:
    """Trace through *provider* (an SDK ``TracerProvider``) from now on."""
    global _RUNTIME
    _RUNTIME = _Runtime(
        tracer          = provider.get_tracer("nerdvana_cli", __version__),
        provider        = provider,
        capture_content = config.capture_content,
        masker          = masker or SecretMasker.from_environment(),
        observers       = weakref.WeakValueDictionary(),
    )


def deactivate() -> None:
    """Stop tracing and flush what the provider still holds. Loops built earlier keep their observers but record nothing new."""
    global _RUNTIME
    runtime, _RUNTIME = _RUNTIME, None
    if runtime is None:
        return
    try:
        runtime.provider.shutdown()
    except Exception:  # noqa: BLE001
        logger.warning("OpenTelemetry shutdown failed", exc_info=True)


def is_active() -> bool:
    """True while tracing is on."""
    return _RUNTIME is not None


def observe_loop(loop: AgentLoop) -> None:
    """Trace *loop* when tracing is on; a no-op otherwise. Called once for each loop as it is built."""
    runtime = _RUNTIME
    if runtime is None:
        return
    try:
        LoopObserver(runtime, loop).attach()
    except Exception:  # noqa: BLE001
        logger.warning("OpenTelemetry could not attach to the agent loop", exc_info=True)


def chain_usage_listeners(first: UsageListener | None, second: UsageListener) -> UsageListener:
    """A usage listener that calls *first* (when there is one) and then *second*."""
    if first is None:
        return second

    def both(info: dict[str, Any]) -> None:
        first(info)
        second(info)

    return both


def trace_environment() -> dict[str, str]:
    """``TRACEPARENT`` (and ``TRACESTATE``) of the tool call being run, for a child process; empty when not tracing."""
    span = _ACTIVE_TOOL_SPAN.get()
    if _RUNTIME is None or span is None or not span.is_recording():
        return {}
    try:
        from opentelemetry import trace
        from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

        carrier: dict[str, str] = {}
        TraceContextTextMapPropagator().inject(carrier, context=trace.set_span_in_context(span))
        return {name.upper(): value for name, value in carrier.items()}
    except Exception:  # noqa: BLE001
        logger.debug("trace context could not be injected", exc_info=True)
        return {}


# ---------------------------------------------------------------------------
# Span helpers: the only code that talks to the OpenTelemetry API
# ---------------------------------------------------------------------------


def _start(runtime: _Runtime, name: str, kind: str, parent: Any, attributes: dict[str, Any], start_ns: int | None = None) -> Any:
    """Start a span below *parent* (a new trace when None); its attributes are set at creation so a sampler sees them."""
    from opentelemetry import trace
    from opentelemetry.context import Context

    context = trace.set_span_in_context(parent) if parent is not None else Context()
    return runtime.tracer.start_span(
        name,
        context    = context,
        kind       = getattr(trace.SpanKind, kind),
        attributes = {key: value for key, value in attributes.items() if value is not None},
        start_time = start_ns,
    )


def _finish(span: Any, end_ns: int, error_type: str | None = None) -> None:
    """End *span* at *end_ns*, marked as failed with ``error.type`` when *error_type* is given."""
    from opentelemetry.trace import Status, StatusCode

    if error_type:
        span.set_attribute(Attr.ERROR_TYPE, error_type)
        span.set_status(Status(StatusCode.ERROR))
    span.end(end_time=end_ns)


def _guarded(handler: Callable[..., Any]) -> Callable[..., Any]:
    """Run *handler* so that nothing it raises reaches the agent."""

    def run(*args: Any, **kwargs: Any) -> Any:
        try:
            return handler(*args, **kwargs)
        except Exception:  # noqa: BLE001
            logger.warning("OpenTelemetry handler %s failed", handler.__name__, exc_info=True)
            return None

    run.__name__ = handler.__name__
    return run


# ---------------------------------------------------------------------------
# Content capture
# ---------------------------------------------------------------------------


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _block_part(block: dict[str, Any]) -> dict[str, Any]:
    """One content block of a message as a typed part."""
    if block.get("type") == "text" and "text" in block:
        return {"type": "text", "content": block["text"]}
    return {"type": str(block.get("type", "blob")), "content": block}


def _message_record(message: Any) -> dict[str, Any]:
    """One history message in the shape of the conventions' input messages: a role and a list of typed parts."""
    content = message.content
    parts   = [{"type": "text", "content": content}] if isinstance(content, str) else [_block_part(block) for block in content]
    if message.tool_use_id:
        parts = [{"type": "tool_call_response", "id": message.tool_use_id, "response": part["content"]} for part in parts]
    parts.extend({"type": "tool_call", "id": call.get("id"), "name": call.get("name"), "arguments": call.get("input")} for call in message.tool_uses)
    return {"role": str(message.role), "parts": parts}


# ---------------------------------------------------------------------------
# One observer per agent loop
# ---------------------------------------------------------------------------


class LoopObserver:
    """Turns the hook events and the usage reports of one ``AgentLoop`` into spans."""

    def __init__(self, runtime: _Runtime, loop: AgentLoop) -> None:
        self._rt                = runtime
        self._loop              = loop
        self._invoke: Any       = None
        self._run_key           = -1
        self._tools: list[_OpenTool] = []
        self._last_ns           = 0
        self._request_ns: int | None = None
        self._history: list[Any] | None = None

    def attach(self) -> None:
        """Register on the loop's hook engine and put this observer on the loop's usage listener."""
        hooks = self._loop.hooks
        hooks.register(HookEvent.BEFORE_API_CALL, self._on_before_api_call)
        hooks.register(HookEvent.AFTER_API_CALL,  self._on_after_api_call)
        hooks.register(HookEvent.BEFORE_TOOL,     self._on_before_tool)
        hooks.register(HookEvent.AFTER_TOOL,      self._on_after_tool)
        hooks.register(HookEvent.SESSION_END,     self._on_session_end)
        self._loop.usage_listener = chain_usage_listeners(self._loop.usage_listener, self.on_usage)

    # -- identity ----------------------------------------------------------

    @property
    def _session_id(self) -> str:
        return str(getattr(self._loop.session, "session_id", ""))

    def _touch(self) -> int:
        self._last_ns = time.time_ns()
        return self._last_ns

    def _parent_for_children(self) -> Any:
        """The span a sub-agent of this session hangs below: the newest open Agent or Swarm call, else the run."""
        for open_tool in reversed(self._tools):
            if open_tool.name in sc.SUBAGENT_TOOLS:
                return open_tool.span
        return self._invoke

    def _parent_of_run(self) -> Any:
        """The span this loop's runs hang below: the tool call of the session that started this agent, if traced."""
        parent_session = self._loop.origin.parent_session_id
        parent = self._rt.observers.get(parent_session) if parent_session else None
        return parent._parent_for_children() if parent is not None else None

    # -- the run -----------------------------------------------------------

    def _ensure_run(self) -> None:
        """Open the ``invoke_agent`` span for the prompt being run; a span left from an earlier prompt is closed at its last event."""
        prompt = getattr(self._loop, "_turn", 0)
        if self._invoke is not None and prompt == self._run_key:
            return
        self._end_run(self._last_ns or time.time_ns())
        origin   = self._loop.origin
        model    = self._loop.settings.model
        now      = self._touch()
        self._run_key = prompt
        self._rt.observers[self._session_id] = self
        self._invoke = _start(
            self._rt, f"{Operation.INVOKE_AGENT} {origin.agent_type}", "INTERNAL", self._parent_of_run(),
            {
                Attr.OPERATION_NAME:  Operation.INVOKE_AGENT,
                Attr.AGENT_NAME:      origin.agent_type,
                Attr.AGENT_ID:        origin.agent_id,
                Attr.PROVIDER_NAME:   sc.provider_name(model.provider),
                Attr.REQUEST_MODEL:   model.model,
                Attr.CONVERSATION_ID: self._session_id,
            },
            now,
        )

    def _end_run(self, end_ns: int) -> None:
        """Close what is still open: tool calls that never reported back, then the run."""
        for open_tool in self._tools:
            _finish(open_tool.span, end_ns, sc.ERROR_ABORTED)
        self._tools.clear()
        if self._invoke is not None:
            _finish(self._invoke, end_ns)
            self._invoke = None

    @_guarded
    def _on_before_api_call(self, ctx: HookContext) -> HookResult | None:
        self._ensure_run()
        self._request_ns = self._touch()
        self._history    = list(ctx.messages) if self._rt.capture_content else None
        return None

    @_guarded
    def _on_after_api_call(self, ctx: HookContext) -> HookResult | None:
        if ctx.stop_reason == "end_turn":
            self._end_run(self._touch())
        return None

    @_guarded
    def _on_session_end(self, ctx: HookContext) -> HookResult | None:
        self._end_run(self._touch())
        self._rt.observers.pop(self._session_id, None)
        return None

    # -- requests ----------------------------------------------------------

    @_guarded
    def on_usage(self, info: dict[str, Any]) -> None:
        """Record the request that just finished as a ``chat`` span, started when it was sent."""
        end_ns = self._touch()
        model  = str(info.get("model", ""))
        attributes: dict[str, Any] = {
            Attr.OPERATION_NAME:      Operation.CHAT,
            Attr.PROVIDER_NAME:       sc.provider_name(str(info.get("provider", ""))),
            Attr.REQUEST_MODEL:       model,
            Attr.CONVERSATION_ID:     self._session_id,
            Attr.USAGE_INPUT_TOKENS:  int(info.get("input_tokens", 0)),
            Attr.USAGE_OUTPUT_TOKENS: int(info.get("output_tokens", 0)),
            Attr.USAGE_CACHE_READ:    int(info.get("cache_read_tokens", 0)),
            Attr.USAGE_CACHE_WRITE:   int(info.get("cache_write_tokens", 0)),
            Attr.COST_USD:            float(info.get("cost_usd", 0.0)),
            Attr.TURN:                int(info.get("turn", 0)),
        }
        if self._history is not None:
            attributes[Attr.INPUT_MESSAGES] = self._rt.text(_dump([_message_record(m) for m in self._history]))
        span = _start(self._rt, f"{Operation.CHAT} {model}", "CLIENT", self._invoke, attributes, self._request_ns or end_ns)
        _finish(span, end_ns)
        self._request_ns = None
        self._history    = None

    # -- tool calls --------------------------------------------------------

    @staticmethod
    def _tool_key(name: str, arguments: dict[str, Any]) -> tuple[str, str]:
        return name, json.dumps(arguments, sort_keys=True, default=str)

    def _start_tool(self, name: str, arguments: dict[str, Any], start_ns: int) -> Any:
        attributes: dict[str, Any] = {
            Attr.OPERATION_NAME:  Operation.EXECUTE_TOOL,
            Attr.TOOL_NAME:       name,
            Attr.TOOL_TYPE:       sc.TOOL_TYPE_FUNCTION,
            Attr.CONVERSATION_ID: self._session_id,
        }
        if self._rt.capture_content:
            attributes[Attr.TOOL_CALL_ARGUMENTS] = self._rt.text(_dump(arguments))
        return _start(self._rt, f"{Operation.EXECUTE_TOOL} {name}", "INTERNAL", self._invoke, attributes, start_ns)

    @_guarded
    def _on_before_tool(self, ctx: HookContext) -> HookResult | None:
        arguments = ctx.tool_input or {}
        span      = self._start_tool(ctx.tool_name, arguments, self._touch())
        self._tools.append(_OpenTool(self._tool_key(ctx.tool_name, arguments), ctx.tool_name, span))
        _ACTIVE_TOOL_SPAN.set(span)
        return None

    @_guarded
    def _on_after_tool(self, ctx: HookContext) -> HookResult | None:
        """Close the span of the finished call, or make one for a call that was refused before it ran."""
        _ACTIVE_TOOL_SPAN.set(None)
        arguments = ctx.tool_input or {}
        key       = self._tool_key(ctx.tool_name, arguments)
        end_ns    = self._touch()
        index     = next((i for i, open_tool in enumerate(self._tools) if open_tool.key == key), None)
        span      = self._tools.pop(index).span if index is not None else self._start_tool(ctx.tool_name, arguments, end_ns)
        result    = ctx.tool_result
        if result is not None:
            span.set_attribute(Attr.TOOL_CALL_ID, result.tool_use_id)
            if self._rt.capture_content:
                span.set_attribute(Attr.TOOL_CALL_RESULT, self._rt.text(str(result.content)))
        _finish(span, end_ns, sc.ERROR_TOOL if result is not None and result.is_error else None)
        return None
