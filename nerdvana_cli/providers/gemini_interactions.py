"""Gemini Interactions API path of the Gemini provider.

Author: 최진호
Date:   2026-10-03

Shapes relied on here. Sources: the Gemini API guides (interactions, thinking, function-calling, streaming) and the
Interactions API reference, with every field name cross-checked against the typed models of the installed google-genai
SDK (``google.genai.interactions``, version 2.28.0). None of it was exercised against the live API.

Request (``client.aio.interactions.create``):
  model, input (a list of steps), system_instruction, tools, store=False, stream=True,
  generation_config={"max_output_tokens", "temperature", "thinking_level", "thinking_summaries"}.
  ``previous_interaction_id`` is not used: every turn sends the full input, which is the stateless mode.

Function tool: {"type": "function", "name", "description", "parameters": JSON schema}.

Input steps:
  user text and images   {"type": "user_input", "content": [{"type": "text", "text"},
                                                             {"type": "image", "data": base64, "mime_type"}]}
  assistant text         {"type": "model_output", "content": [{"type": "text", "text"}]}
  function call          {"type": "function_call", "id", "name", "arguments": object}
  call result            {"type": "function_result", "call_id", "name", "result": [{"type": "text", "text"}], "is_error"}
  thought                {"type": "thought", "signature", "summary": [{"type": "text", "text"}]}
  A call is paired with its result by ``call_id`` (the id the server put on the call). The function call step has
  no signature of its own: the signature lives on the thought step, and in stateless mode every thought step must be
  sent back unchanged. Thoughts are kept as provider blocks ({"type": "thought", "signature", "summary", "pos"}) where
  ``pos`` is the number of calls that preceded the thought, so replay restores the order the model produced.

Stream events (each carries event_type; step events carry the step ``index``):
  step.start    step (type function_call: id, name, arguments; thought; model_output)
  step.delta    delta: text | thought_summary (content) | thought_signature (signature) | arguments_delta (JSON fragment)
  step.stop     the step is complete
  interaction.completed   interaction (status, usage)
  error         error (code, message)
  Every other event (interaction.created, interaction.status_update, ...) is ignored.

Status: completed and requires_action end the turn (tool_use when calls were made), incomplete means the output limit
  was hit (max_tokens), failed and cancelled are errors.

Usage (interaction.usage): total_input_tokens (the whole prompt, cached part included), total_cached_tokens,
  total_output_tokens, total_thought_tokens (counted as output, as the thinking guide bills them).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from typing import Any

from nerdvana_cli.providers.base import ProviderConfig, ProviderEvent
from nerdvana_cli.providers.errors import RETRYABLE, error_fields
from nerdvana_cli.providers.gemini_provider import GeminiProvider, _gemini_int
from nerdvana_cli.providers.openai_provider import parse_arguments
from nerdvana_cli.types import ToolSpec

_PLACEHOLDER = "[tool execution]"
_TRUNCATED   = frozenset({"incomplete", "budget_exceeded"})
_FAILED      = frozenset({"failed", "cancelled"})


def uses_interactions(config: ProviderConfig) -> bool:
    """Report whether *config* selects the Interactions API: ``auto`` picks it."""
    return config.gemini_api in ("auto", "interactions")


def _stop_reason(status: str, called: bool) -> str:
    """Why the interaction ended: a truncated one is ``max_tokens``, otherwise the calls decide."""
    if status in _TRUNCATED:
        return "max_tokens"
    return "tool_use" if called else "end_turn"


def _usage(usage: Any) -> dict[str, int]:
    """Normalise ``interaction.usage``; the cached part of the prompt is reported as ``cache_read_tokens``."""
    cached = _gemini_int(getattr(usage, "total_cached_tokens", 0))
    result = {
        "input_tokens":  _gemini_int(getattr(usage, "total_input_tokens", 0)),
        "output_tokens": _gemini_int(getattr(usage, "total_output_tokens", 0)) + _gemini_int(getattr(usage, "total_thought_tokens", 0)),
    }
    if cached:
        result["cache_read_tokens"] = cached
    return result


def _text(text: str) -> dict[str, Any]:
    """A text content block."""
    return {"type": "text", "text": text}


def _thought_block(signature: str, summary: list[str], pos: int) -> dict[str, Any]:
    """A thought step as a provider block, to be replayed unchanged; *pos* is the number of calls before it."""
    block: dict[str, Any] = {"type": "thought", "signature": signature, "pos": pos}
    if summary:
        block["summary"] = [_text(part) for part in summary]
    return block


def _texts(contents: Any) -> list[str]:
    """The non-empty text of each text content block in *contents*."""
    return [c.text for c in contents or () if getattr(c, "type", "") == "text" and c.text]


# ---------------------------------------------------------------------------
# Messages to steps
# ---------------------------------------------------------------------------


def _user_input(blocks: list[dict[str, Any]]) -> dict[str, Any]:
    """Text and image blocks as one ``user_input`` step."""
    content: list[dict[str, Any]] = []
    for block in blocks:
        if block.get("type") == "image":
            content.append({"type": "image", "data": block["data"], "mime_type": block["media_type"]})
        elif block.get("type") == "text":
            content.append(_text(block.get("text", "")))
    return {"type": "user_input", "content": content}


def _result_step(source: dict[str, Any], content: Any, names_by_id: dict[str, str]) -> dict[str, Any]:
    """A ``function_result`` step for a tool result in *source*; the function name is sent when it can be resolved."""
    call_id = source.get("tool_use_id", "")
    name    = GeminiProvider._resolve_tool_name(source, call_id, names_by_id)
    step: dict[str, Any] = {"type": "function_result", "call_id": call_id, "result": [_text(content if isinstance(content, str) else str(content))]}
    if name != "unknown":
        step["name"] = name
    if source.get("is_error"):
        step["is_error"] = True
    return step


def _user_steps(blocks: list[dict[str, Any]], names_by_id: dict[str, str]) -> list[dict[str, Any]]:
    """A user block list as steps: tool results become ``function_result`` steps, the rest user input."""
    steps:   list[dict[str, Any]] = []
    pending: list[dict[str, Any]] = []
    for block in blocks:
        if block.get("type") != "tool_result":
            pending.append(block)
            continue
        if pending:
            steps.append(_user_input(pending))
            pending = []
        steps.append(_result_step(block, block.get("content", ""), names_by_id))
    if pending:
        steps.append(_user_input(pending))
    return steps


def _thought_step(block: dict[str, Any]) -> dict[str, Any]:
    step: dict[str, Any] = {"type": "thought", "signature": block["signature"]}
    if block.get("summary"):
        step["summary"] = block["summary"]
    return step


def _assistant_steps(msg: dict[str, Any], names_by_id: dict[str, str]) -> list[dict[str, Any]]:
    """An assistant turn as steps: thoughts and text first, each call after the thoughts that preceded it."""
    thoughts = [(b.get("pos", 0), _thought_step(b)) for b in msg.get("provider_blocks") or [] if b.get("type") == "thought" and b.get("signature")]
    calls    = msg.get("tool_uses") or []
    content  = msg.get("content", "")
    text     = content if isinstance(content, str) else str(content or "")
    steps    = [step for pos, step in thoughts if pos <= 0]
    if text and not (text == _PLACEHOLDER and calls):
        steps.append({"type": "model_output", "content": [_text(text)]})
    for index, call in enumerate(calls):
        GeminiProvider._register_call(call.get("id", ""), call.get("name", ""), names_by_id)
        if index:
            steps.extend(step for pos, step in thoughts if pos == index)
        steps.append({"type": "function_call", "id": call.get("id", ""), "name": call.get("name", ""), "arguments": call.get("input") or {}})
    steps.extend(step for pos, step in thoughts if pos > 0 and pos >= len(calls))
    return steps


def convert_input(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert internal messages to Interactions input steps."""
    steps:       list[dict[str, Any]] = []
    names_by_id: dict[str, str]       = {}
    for msg in messages:
        role    = msg.get("role", "user")
        content = msg.get("content", "")
        if role == "assistant":
            steps.extend(_assistant_steps(msg, names_by_id))
        elif role == "tool":
            steps.append(_result_step(msg, content, names_by_id))
        elif role == "user" and isinstance(content, list):
            steps.extend(_user_steps(content, names_by_id))
        else:
            steps.append(_user_input([{"type": "text", "text": str(content)}]))
    return steps


# ---------------------------------------------------------------------------
# Stream events to ProviderEvents
# ---------------------------------------------------------------------------


def _stream_error(error: Any) -> ProviderEvent:
    """The error event for an ``error`` stream event, classified from its message."""
    message = getattr(error, "message", None) or getattr(error, "code", None) or "interaction stream failed"
    return ProviderEvent(type="error", error=message, **error_fields(RuntimeError(message)))


class _InteractionStream:
    """Folds Interactions stream events into ProviderEvents; ``finish`` yields what only the whole interaction settles."""

    def __init__(self) -> None:
        self._open:     dict[int, dict[str, Any]] = {}
        self._calls:    list[ProviderEvent]       = []
        self._started                             = 0
        self._terminal: Any                       = None
        self._failed                              = False

    def feed(self, event: Any) -> list[ProviderEvent]:
        """The events one stream event yields."""
        kind = getattr(event, "event_type", "")
        if self._failed:
            return []
        if kind == "step.start":
            return self._start(event.index, event.step)
        if kind == "step.delta":
            return self._delta(event.index, event.delta)
        if kind == "step.stop":
            return self._stop(event.index)
        if kind == "interaction.completed":
            self._terminal = event.interaction
        elif kind == "error":
            self._failed = True
            return [_stream_error(event.error)]
        return []

    def _start(self, index: int, step: Any) -> list[ProviderEvent]:
        kind = getattr(step, "type", "")
        state: dict[str, Any] = {"type": kind}
        self._open[index] = state
        if kind == "function_call":
            state.update(id=step.id, name=step.name, arguments=dict(step.arguments or {}), fragments=[])
            self._started += 1
        elif kind == "thought":
            state.update(signature=getattr(step, "signature", None) or "", summary=[], pos=self._started)
            return self._summary(state, getattr(step, "summary", None))
        elif kind == "model_output":
            return [ProviderEvent(type="content_delta", content=text) for text in _texts(getattr(step, "content", None))]
        return []

    def _delta(self, index: int, delta: Any) -> list[ProviderEvent]:
        kind  = getattr(delta, "type", "")
        state = self._open.get(index)
        if kind == "text":
            return [ProviderEvent(type="content_delta", content=delta.text)] if delta.text else []
        if state is None:
            return []
        if kind == "thought_signature":
            state["signature"] = delta.signature or state["signature"]
        elif kind == "thought_summary":
            return self._summary(state, [delta.content] if delta.content else None)
        elif kind == "arguments_delta":
            state["fragments"].append(delta.arguments or "")
        return []

    @staticmethod
    def _summary(state: dict[str, Any], contents: Any) -> list[ProviderEvent]:
        """Thinking text from summary content blocks; kept on the step so the replayed thought carries it."""
        texts = _texts(contents)
        state["summary"].extend(texts)
        return [ProviderEvent(type="thinking_delta", thinking=text) for text in texts]

    def _stop(self, index: int) -> list[ProviderEvent]:
        state = self._open.pop(index, None)
        if state is None:
            return []
        if state["type"] == "function_call":
            raw = "".join(state["fragments"])
            self._calls.append(ProviderEvent(
                type                = "tool_use_complete",
                tool_use_id         = state["id"],
                tool_name           = state["name"],
                tool_input_complete = parse_arguments(raw) if raw else state["arguments"],
            ))
        elif state["type"] == "thought" and state["signature"]:
            return [ProviderEvent(type="provider_block", block=_thought_block(state["signature"], state["summary"], state["pos"]))]
        return []

    def finish(self) -> list[ProviderEvent]:
        """The tool calls, usage and the stop reason; an error when the stream ended early or the interaction failed."""
        if self._failed:
            return []
        if self._terminal is None:
            return [ProviderEvent(type="error", error="stream ended before the interaction completed", error_kind=RETRYABLE)]
        status = self._terminal.status
        if status in _FAILED:
            return [ProviderEvent(type="error", error=f"interaction {status}")]
        stop   = _stop_reason(status, bool(self._calls))
        events = list(self._calls) if stop == "tool_use" else []
        if getattr(self._terminal, "usage", None):
            events.append(ProviderEvent(type="usage", usage=_usage(self._terminal.usage)))
        events.append(ProviderEvent(type="done", stop_reason=stop))
        return events


def _send_result(interaction: Any) -> dict[str, Any]:
    """A non-streaming Interaction as the dict ``send`` returns."""
    status = interaction.status
    if status in _FAILED:
        return {"content": f"interaction {status}", "tool_uses": [], "usage": {}, "is_error": True}
    content                         = ""
    tool_uses: list[dict[str, Any]] = []
    blocks:    list[dict[str, Any]] = []
    for step in interaction.steps or ():
        if step.type == "model_output":
            content += "".join(_texts(step.content))
        elif step.type == "function_call":
            tool_uses.append({"id": step.id, "name": step.name, "input": dict(step.arguments or {})})
        elif step.type == "thought" and step.signature:
            blocks.append(_thought_block(step.signature, _texts(step.summary), len(tool_uses)))
    result: dict[str, Any] = {
        "content":     content,
        "tool_uses":   tool_uses,
        "stop_reason": _stop_reason(status, bool(tool_uses)),
        "usage":       _usage(interaction.usage) if getattr(interaction, "usage", None) else {},
    }
    if blocks:
        result["provider_blocks"] = blocks
    return result


class GeminiInteractionsProvider(GeminiProvider):
    """Gemini provider on the Interactions API, stateless: every turn sends the full input with ``store`` false."""

    def _generation_config(self) -> dict[str, Any]:
        """Output limit, temperature and the thinking level; summaries are asked for when a level is set and thinking is shown."""
        cfg: dict[str, Any] = {"max_output_tokens": self.config.max_tokens, "temperature": self.config.temperature}
        level = self._thinking_level()
        if level:
            cfg["thinking_level"] = level.lower()
            if self.config.show_thinking:
                cfg["thinking_summaries"] = "auto"
        return cfg

    def _request(self, system_prompt: str, messages: list[dict[str, Any]], tools: Sequence[ToolSpec]) -> dict[str, Any]:
        """The ``interactions.create`` arguments for one turn."""
        request: dict[str, Any] = {
            "model":             self.config.model,
            "input":             convert_input(messages),
            "store":             False,
            "generation_config": self._generation_config(),
        }
        if system_prompt:
            request["system_instruction"] = system_prompt
        if tools:
            request["tools"] = [
                {"type": "function", "name": t.name, "description": t.description_text, "parameters": t.input_schema} for t in tools
            ]
        return request

    async def stream(
        self,
        system_prompt: str,
        messages: list[dict[str, Any]],
        tools: Sequence[ToolSpec],
    ) -> AsyncIterator[ProviderEvent]:
        """Stream one interaction; yields the same events as the generateContent path, plus thought blocks."""
        try:
            client = self._get_client()
        except ImportError:
            yield ProviderEvent(type="error", error="google-genai not installed. Run: pip install google-genai")
            return
        try:
            events = await client.aio.interactions.create(**self._request(system_prompt, messages, tools), stream=True)
            folded = _InteractionStream()
            async for event in events:
                for out in folded.feed(event):
                    yield out
            for out in folded.finish():
                yield out
        except Exception as e:
            yield ProviderEvent(type="error", error=str(e), **error_fields(e))

    async def send(
        self,
        system_prompt: str,
        messages: list[dict[str, Any]],
        tools: Sequence[ToolSpec],
    ) -> dict[str, Any]:
        """Non-streaming interaction."""
        try:
            client = self._get_client()
        except ImportError:
            return {"content": "", "tool_uses": [], "usage": {}, "is_error": True}
        try:
            interaction = await client.aio.interactions.create(**self._request(system_prompt, messages, tools))
        except Exception as e:
            return {"content": str(e), "tool_uses": [], "usage": {}, "is_error": True}
        return _send_result(interaction)
