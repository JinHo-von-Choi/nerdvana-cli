"""OpenAI Responses API path of the OpenAI provider.

Author: 최진호
Date:   2026-10-03

Shapes relied on here. Sources: the OpenAI guides (migrate-to-responses, function-calling, reasoning,
streaming-responses) and API reference (responses create, streaming events), with every field name
cross-checked against the typed models of the installed openai SDK. None of it was exercised against
the live API.

Request (``client.responses.create``):
  model, instructions (system prompt), input (list of Items), tools, stream=True, store=False,
  max_output_tokens, temperature, reasoning={"effort", "summary"}, include=["reasoning.encrypted_content"].
  The ``include`` value is legacy: the API still accepts it, and in stateless mode (store false) reasoning
  items carry ``encrypted_content`` without it. It is sent anyway when reasoning is requested so older
  reasoning models work too. ``previous_response_id`` is not used: every turn sends the full input.

Function tool: flat, {"type": "function", "name", "description", "parameters", "strict"}. ``strict`` is
  sent as false: omitting it makes the API attempt strict mode, which rewrites schemas we do not control.

Input Items:
  user text         {"role": "user", "content": str | [{"type": "input_text", "text"},
                                                       {"type": "input_image", "image_url", "detail"}]}
  assistant text    {"role": "assistant", "content": str}
  function call     {"type": "function_call", "call_id", "name", "arguments": JSON string}
  call result       {"type": "function_call_output", "call_id", "output": str}
  reasoning         {"type": "reasoning", "id", "summary": [{"type": "summary_text", "text"}], "encrypted_content"}
  A call is paired with its result by ``call_id`` (the call also has an ``id`` of its own, which is not sent).
  Reasoning items returned with tool calls must be passed back with the tool outputs. Without
  ``encrypted_content`` a stateless request cannot resolve an item, so only items that carry it are kept.

Stream events (all carry sequence_number; ``item`` is a typed output item):
  response.output_text.delta               delta
  response.reasoning_summary_text.delta    delta, item_id, output_index, summary_index
  response.reasoning_text.delta            delta  (raw reasoning of open-weight models)
  response.output_item.done                item (type "function_call": call_id, name, arguments;
                                                 type "reasoning": id, summary, encrypted_content), output_index
  response.completed | response.incomplete | response.failed
                                           response (status, usage, incomplete_details.reason, error.code/message)
  error                                    code, message, param
  Every other event (created, in_progress, output_item.added, content_part.*, output_text.done,
  function_call_arguments.*, ...) repeats what output_item.done delivers whole and is ignored.

Usage (response.usage): input_tokens (the whole prompt, cached part included),
  input_tokens_details.cached_tokens, output_tokens (reasoning tokens included), output_tokens_details.reasoning_tokens.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Sequence
from typing import Any

from nerdvana_cli.providers.base import ProviderConfig, ProviderEvent
from nerdvana_cli.providers.errors import CONTEXT_LIMIT, OTHER, RETRYABLE
from nerdvana_cli.providers.openai_provider import (
    OpenAIProvider,
    _error_event,
    _int,
    _safe_str,
    is_official_openai,
    parse_arguments,
)
from nerdvana_cli.providers.thinking_parser import ThinkBlockParser
from nerdvana_cli.types import ToolSpec

_PLACEHOLDER = "[tool execution]"
_ERROR_KINDS = {
    "server_error":            RETRYABLE,
    "rate_limit_exceeded":     RETRYABLE,
    "vector_store_timeout":    RETRYABLE,
    "context_length_exceeded": CONTEXT_LIMIT,
}


def uses_responses(config: ProviderConfig) -> bool:
    """Report whether *config* selects the Responses API: ``auto`` means OpenAI's own endpoint only."""
    if config.openai_api == "auto":
        return is_official_openai(config)
    return config.openai_api == "responses"


def _reasoning_block(item: Any) -> dict[str, Any] | None:
    """A reasoning output item as a provider block, or None when it cannot be replayed (no encrypted content)."""
    encrypted = getattr(item, "encrypted_content", None)
    if not encrypted:
        return None
    return {
        "type":              "reasoning",
        "id":                getattr(item, "id", ""),
        "summary":           [{"type": "summary_text", "text": part.text} for part in getattr(item, "summary", None) or ()],
        "encrypted_content": encrypted,
    }


def _usage(usage: Any) -> dict[str, int]:
    """Normalise ``response.usage``; the cached part of the prompt is reported as ``cache_read_tokens``."""
    cached = _int(getattr(getattr(usage, "input_tokens_details", None), "cached_tokens", 0))
    result = {
        "input_tokens":  _int(getattr(usage, "input_tokens", 0)),
        "output_tokens": _int(getattr(usage, "output_tokens", 0)),
    }
    if cached:
        result["cache_read_tokens"] = cached
    return result


def _failure(code: str | None, message: str | None) -> ProviderEvent:
    """The error event for a failed response or an ``error`` stream event."""
    return ProviderEvent(type="error", error=message or code or "response failed", error_kind=_ERROR_KINDS.get(code or "", OTHER))


def _input_parts(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Text and image blocks as Responses content parts (images as data URLs)."""
    parts: list[dict[str, Any]] = []
    for block in blocks:
        if block.get("type") == "image":
            parts.append({"type": "input_image", "image_url": f"data:{block['media_type']};base64,{block['data']}", "detail": "auto"})
        elif block.get("type") == "text":
            parts.append({"type": "input_text", "text": block.get("text", "")})
    return parts


def _assistant_items(msg: dict[str, Any]) -> list[dict[str, Any]]:
    """An assistant turn as Items: its reasoning first, then its text, then its calls."""
    items: list[dict[str, Any]] = [dict(b) for b in msg.get("provider_blocks") or [] if b.get("type") == "reasoning"]
    tool_uses = msg.get("tool_uses") or []
    content   = msg.get("content", "")
    text      = content if isinstance(content, str) else _safe_str(content)
    if text and not (text == _PLACEHOLDER and tool_uses):
        items.append({"role": "assistant", "content": text})
    for tu in tool_uses:
        items.append({
            "type":      "function_call",
            "call_id":   _safe_str(tu.get("id", "")),
            "name":      _safe_str(tu.get("name", "")),
            "arguments": json.dumps(tu.get("input") or {}, ensure_ascii=False, default=str),
        })
    return items


def convert_input(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert internal messages to Responses input Items."""
    items: list[dict[str, Any]] = []
    for msg in messages:
        role    = msg.get("role", "user")
        content = msg.get("content", "")
        if role == "assistant":
            items.extend(_assistant_items(msg))
        elif role == "tool":
            items.append({"type": "function_call_output", "call_id": _safe_str(msg.get("tool_use_id", "")), "output": _safe_str(content)})
        elif role == "user" and isinstance(content, list) and any(b.get("type") == "image" for b in content if isinstance(b, dict)):
            items.append({"role": "user", "content": _input_parts(content)})
        else:
            items.append({"role": role, "content": content if isinstance(content, str) else _safe_str(content)})
    return items


class _ResponseStream:
    """Folds Responses stream events into ProviderEvents; ``finish`` yields what only the whole response settles."""

    def __init__(self) -> None:
        self._parser                = ThinkBlockParser()
        self._calls: dict[str, Any] = {}
        self._terminal: Any         = None
        self._failed                = False

    def feed(self, event: Any) -> list[ProviderEvent]:
        """The events one stream event yields."""
        kind = getattr(event, "type", "")
        if self._failed:
            return []
        if kind == "response.output_text.delta":
            parsed = self._parser.feed(event.delta)
            events: list[ProviderEvent] = []
            if parsed.content:
                events.append(ProviderEvent(type="content_delta", content=parsed.content))
            if parsed.thinking:
                events.append(ProviderEvent(type="thinking_delta", thinking=parsed.thinking))
            return events
        if kind in ("response.reasoning_summary_text.delta", "response.reasoning_text.delta"):
            return [ProviderEvent(type="thinking_delta", thinking=event.delta)]
        if kind == "response.output_item.done":
            return self._item_done(event.item)
        if kind in ("response.completed", "response.incomplete"):
            self._terminal = event
        elif kind == "response.failed":
            self._failed = True
            error = getattr(event.response, "error", None)
            return [_failure(getattr(error, "code", None), getattr(error, "message", None))]
        elif kind == "error":
            self._failed = True
            return [_failure(event.code, event.message)]
        return []

    def _item_done(self, item: Any) -> list[ProviderEvent]:
        if item.type == "function_call":
            self._calls.setdefault(item.call_id, item)
        elif item.type == "reasoning" and (block := _reasoning_block(item)):
            return [ProviderEvent(type="provider_block", block=block)]
        return []

    def finish(self) -> list[ProviderEvent]:
        """Remaining text, the tool calls, usage and the stop reason; an error when the stream ended early."""
        if self._failed:
            return []
        if self._terminal is None:
            return [ProviderEvent(type="error", error="stream ended before the response completed", error_kind=RETRYABLE)]
        events: list[ProviderEvent] = []
        final = self._parser.flush()
        if final.content:
            events.append(ProviderEvent(type="content_delta", content=final.content))
        if final.thinking:
            events.append(ProviderEvent(type="thinking_delta", thinking=final.thinking))
        response = self._terminal.response
        stop     = self._stop_reason(response)
        if stop == "tool_use":
            events.extend(
                ProviderEvent(type="tool_use_complete", tool_use_id=call.call_id, tool_name=call.name, tool_input_complete=parse_arguments(call.arguments))
                for call in self._calls.values()
            )
        if getattr(response, "usage", None):
            events.append(ProviderEvent(type="usage", usage=_usage(response.usage)))
        events.append(ProviderEvent(type="done", stop_reason=stop))
        return events

    def _stop_reason(self, response: Any) -> str:
        if self._terminal.type == "response.incomplete":
            reason = getattr(getattr(response, "incomplete_details", None), "reason", None)
            return "max_tokens" if reason in (None, "max_output_tokens") else str(reason)
        return "tool_use" if self._calls else "end_turn"


class OpenAIResponsesProvider(OpenAIProvider):
    """OpenAI provider on the Responses API, stateless: every turn sends the full input with ``store`` false."""

    def _build_tools(self, tools: Sequence[ToolSpec]) -> list[dict[str, Any]]:
        """Flat function tool definitions."""
        return [
            {"type": "function", "name": t.name, "description": t.description_text, "parameters": t.input_schema, "strict": False}
            for t in tools
        ]

    def _request(self, system_prompt: str, messages: list[dict[str, Any]], tools: Sequence[ToolSpec]) -> dict[str, Any]:
        """The ``responses.create`` arguments for one turn."""
        cfg     = self.config
        request: dict[str, Any] = {
            "model":             cfg.model,
            "input":             convert_input(messages),
            "store":             False,
            "max_output_tokens": cfg.max_tokens,
        }
        if system_prompt:
            request["instructions"] = system_prompt
        if tools:
            request["tools"] = self._build_tools(tools)
        effort = cfg.reasoning_effort
        if effort:
            request["reasoning"] = {"effort": effort}
        if effort and effort.lower() != "none":
            request["include"] = ["reasoning.encrypted_content"]
            if cfg.show_thinking:
                request["reasoning"]["summary"] = "auto"
        else:
            request["temperature"] = cfg.temperature
        return request

    async def stream(
        self,
        system_prompt: str,
        messages: list[dict[str, Any]],
        tools: Sequence[ToolSpec],
    ) -> AsyncIterator[ProviderEvent]:
        """Stream one response; yields the same events as the chat completions path."""
        try:
            client = self._get_client()
        except ImportError:
            yield ProviderEvent(type="error", error="openai package not installed. Run: pip install openai")
            return
        try:
            events = await client.responses.create(**self._request(system_prompt, messages, tools), stream=True)
            folded = _ResponseStream()
            async for event in events:
                for out in folded.feed(event):
                    yield out
            for out in folded.finish():
                yield out
        except Exception as e:
            yield _error_event(e)

    async def send(
        self,
        system_prompt: str,
        messages: list[dict[str, Any]],
        tools: Sequence[ToolSpec],
    ) -> dict[str, Any]:
        """Non-streaming response."""
        try:
            client = self._get_client()
        except ImportError:
            return {"content": "openai package not installed", "is_error": True}
        try:
            response = await client.responses.create(**self._request(system_prompt, messages, tools))
        except Exception as e:
            return {"content": str(e), "is_error": True}
        return _send_result(response)


def _send_result(response: Any) -> dict[str, Any]:
    """A non-streaming Response as the dict ``send`` returns."""
    content                         = ""
    tool_uses: list[dict[str, Any]] = []
    blocks:    list[dict[str, Any]] = []
    for item in response.output or ():
        if item.type == "message":
            content += "".join(part.text for part in item.content if part.type == "output_text")
        elif item.type == "function_call":
            tool_uses.append({"id": item.call_id, "name": item.name, "input": parse_arguments(item.arguments)})
        elif item.type == "reasoning" and (block := _reasoning_block(item)):
            blocks.append(block)
    incomplete = getattr(getattr(response, "incomplete_details", None), "reason", None)
    result: dict[str, Any] = {
        "content":     content,
        "tool_uses":   tool_uses,
        "stop_reason": "tool_use" if tool_uses else ("max_tokens" if incomplete else "end_turn"),
        "usage":       _usage(response.usage) if getattr(response, "usage", None) else {},
    }
    if blocks:
        result["provider_blocks"] = blocks
    return result
