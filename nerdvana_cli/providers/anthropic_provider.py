"""Anthropic Claude provider: native API integration."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Sequence
from typing import Any

from rich.console import Console

from nerdvana_cli.providers.anthropic_features import (
    BETA_COMPACTION,
    ECHOED_BLOCKS,
    EffortTracker,
    beta_headers,
    declare_tools,
    effort_message,
    plain,
    supports_compaction,
    with_beta,
)
from nerdvana_cli.providers.base import ProviderConfig, ProviderEvent, ProviderName
from nerdvana_cli.providers.errors import error_fields
from nerdvana_cli.types import ToolSpec

try:
    from anthropic import AsyncAnthropic
except ImportError:  # pragma: no cover (runtime guard in _get_client)
    AsyncAnthropic = None  # type: ignore[assignment,misc]

# Block types that may carry a cache breakpoint.
_CACHEABLE_BLOCKS = frozenset({"text", "tool_use", "tool_result", "image"})
_EPHEMERAL: dict[str, str] = {"type": "ephemeral"}


def _field(obj: Any, name: str, default: Any = None) -> Any:
    """Attribute *name* of an SDK object, or key *name* of a dict."""
    return obj.get(name, default) if isinstance(obj, dict) else getattr(obj, name, default)


def _count(obj: Any, name: str) -> int:
    """An integer usage counter from *obj*, 0 when absent or not a number."""
    value = _field(obj, name, 0)
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _thinking_tokens(usage: Any) -> int:
    """Tokens the model spent thinking, from ``usage.output_tokens_details`` (already inside ``output_tokens``)."""
    return _count(_field(usage, "output_tokens_details"), "thinking_tokens")


def _compaction_tokens(usage: Any) -> tuple[int, int]:
    """Input and output tokens of the summarizing call, from the ``compaction`` entry of ``usage.iterations``."""
    iterations = _field(usage, "iterations")
    spent      = [it for it in iterations if _field(it, "type") == "compaction"] if isinstance(iterations, list) else []
    return sum(_count(it, "input_tokens") for it in spent), sum(_count(it, "output_tokens") for it in spent)


def _usage_dict(usage: Any, output_tokens: int | None = None) -> dict[str, int]:
    """Normalise an Anthropic usage object.

    ``input_tokens`` of the result is the whole prompt: Anthropic reports only the
    tokens after the last cache breakpoint there, with cache writes and reads
    counted separately. ``thinking_tokens`` is the part of ``output_tokens`` spent
    thinking, present only when the API reports it.
    """
    fresh = _count(usage, "input_tokens")
    write = _count(usage, "cache_creation_input_tokens")
    read  = _count(usage, "cache_read_input_tokens")
    result = {
        "input_tokens":  fresh + write + read,
        "output_tokens": _count(usage, "output_tokens") if output_tokens is None else output_tokens,
    }
    if read:
        result["cache_read_tokens"] = read
    if write:
        result["cache_write_tokens"] = write
    if thinking := _thinking_tokens(usage):
        result["thinking_tokens"] = thinking
    return result


# Model families, by id prefix (https://platform.claude.com/docs/en/build-with-claude/thinking).
# Thinking is on by default and rejects a manual budget (a 400) on these.
_THINKING_DEFAULT_ON = ("claude-fable-5", "claude-mythos-5", "claude-opus-5", "claude-sonnet-5")
# Thinking is off until asked for with ``adaptive``.
_THINKING_OPT_IN     = ("claude-opus-4-8", "claude-opus-4-7", "claude-opus-4-6", "claude-sonnet-4-6")
# Non-default temperature is a 400 on every request for these.
_FIXED_SAMPLING      = _THINKING_DEFAULT_ON + ("claude-opus-4-8", "claude-opus-4-7")


def _api_block(block: dict[str, Any]) -> dict[str, Any]:
    """A content block in the API's shape; the internal image block becomes a base64 image source."""
    if block.get("type") == "image":
        return {"type": "image", "source": {"type": "base64", "media_type": block["media_type"], "data": block["data"]}}
    return dict(block)


def _redacted_block(block: Any) -> dict[str, Any]:
    """A redacted thinking block in the shape the API expects back."""
    return {"type": "redacted_thinking", "data": getattr(block, "data", "")}


def _extend_thinking(current: dict[str, Any] | None, delta: Any) -> None:
    """Fold a thinking or signature delta into the thinking block being streamed."""
    if current is None:
        return
    if delta.type == "thinking_delta":
        current["thinking"] += delta.thinking or ""
    else:
        current["signature"] = delta.signature


def request_options(model: str, max_tokens: int, temperature: float, extended: bool, budget: int, show: bool) -> dict[str, Any]:
    """Thinking, token limit and sampling fields for a request to *model*.

    Only ``claude-*`` ids are interpreted; any other id (an Anthropic-compatible
    endpoint) gets the plain request it always got. The returned dict holds
    ``max_tokens``, optionally ``thinking``, and ``temperature`` when allowed.
    """
    options: dict[str, Any] = {"max_tokens": max_tokens}
    if not model.startswith("claude-"):
        options["temperature"] = temperature
        return options
    display = "summarized" if show else "omitted"
    if model.startswith(_THINKING_DEFAULT_ON):
        options["thinking"] = {"type": "adaptive", "display": display}
    elif model.startswith(_THINKING_OPT_IN):
        if extended:
            options["thinking"] = {"type": "adaptive", "display": display}
    elif extended:
        # A model that only takes a manual budget; the budget must stay below max_tokens
        # and the API requires the default temperature, so none is sent.
        options["thinking"]   = {"type": "enabled", "budget_tokens": budget}
        options["max_tokens"] = max(max_tokens, budget + 1024)
        return options
    if not model.startswith(_FIXED_SAMPLING):
        options["temperature"] = temperature
    return options


def with_cache_breakpoints(
    system_prompt: str,
    api_tools:     list[dict[str, Any]],
    api_messages:  list[dict[str, Any]],
) -> tuple[Any, list[dict[str, Any]], list[dict[str, Any]]]:
    """Mark the prompt prefix for caching; returns (system, tools, messages).

    The prefix is built tools, then system, then messages, so breakpoints go on the
    last tool, the system prompt, and the last block of the conversation. That is
    three of the four allowed. A tool deferred by server-side search cannot carry a
    breakpoint, so the last tool that is not deferred does. Inputs are not modified.
    """
    tools = [dict(tool) for tool in api_tools]
    loaded = [tool for tool in tools if not tool.get("defer_loading")]
    if loaded:
        loaded[-1]["cache_control"] = dict(_EPHEMERAL)

    system: Any = system_prompt
    if system_prompt:
        system = [{"type": "text", "text": system_prompt, "cache_control": dict(_EPHEMERAL)}]

    messages = [dict(message) for message in api_messages]
    if messages and isinstance(messages[-1].get("content"), list):
        blocks = [dict(block) for block in messages[-1]["content"]]
        for block in reversed(blocks):
            if block.get("type") in _CACHEABLE_BLOCKS:
                block["cache_control"] = dict(_EPHEMERAL)
                break
        messages[-1]["content"] = blocks
    return system, tools, messages


console = Console()

# Longest a compaction request may run; the summary of a long conversation takes a while to write.
_COMPACTION_TIMEOUT = 600.0


def _tool_input(raw: str) -> dict[str, Any]:
    """Tool arguments streamed as JSON text: empty when none arrived or the text is not valid JSON."""
    try:
        return json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        return {}


def _provider_block(block: dict[str, Any]) -> ProviderEvent:
    """An event carrying a content block that must be sent back unchanged with the assistant turn."""
    return ProviderEvent(type="provider_block", block=block)


class _Turn:
    """Folds the events of one streamed response into provider events, remembering the block being built."""

    def __init__(self) -> None:
        self.usage:       dict[str, int] = {}
        self.stop_reason: str            = "end_turn"
        self._tool:         dict[str, Any]        = {}
        self._tool_input:   str                   = ""
        self._thinking:     dict[str, Any] | None = None
        self._server:       dict[str, Any] | None = None
        self._server_input: str                   = ""

    def feed(self, event: Any) -> list[ProviderEvent]:
        """The provider events *event* produces; stream events with no meaning for the agent produce none."""
        handler = getattr(self, f"_on_{event.type}", None)
        return handler(event) if handler else []

    def _on_message_start(self, event: Any) -> list[ProviderEvent]:
        message = getattr(event, "message", None)
        if message and hasattr(message, "usage") and message.usage:
            self.usage = _usage_dict(message.usage)
        return []

    def _on_content_block_start(self, event: Any) -> list[ProviderEvent]:
        block = event.content_block
        if block.type == "thinking":
            self._thinking = {"type": "thinking", "thinking": "", "signature": ""}
        elif block.type == "redacted_thinking":
            return [_provider_block(_redacted_block(block))]
        elif block.type == "tool_use":
            self._tool, self._tool_input = {"id": block.id, "name": block.name}, ""
            return [ProviderEvent(type="tool_use_start", tool_use_id=block.id, tool_name=block.name)]
        elif block.type == "server_tool_use":
            self._server, self._server_input = {"type": "server_tool_use", "id": block.id, "name": block.name}, ""
        elif block.type in ("tool_search_tool_result", "compaction"):
            return [_provider_block(plain(block))]
        return []

    def _on_content_block_delta(self, event: Any) -> list[ProviderEvent]:
        delta = event.delta
        if delta.type == "text_delta":
            return [ProviderEvent(type="content_delta", content=delta.text)]
        if delta.type in ("thinking_delta", "signature_delta"):
            _extend_thinking(self._thinking, delta)
            return [ProviderEvent(type="thinking_delta", thinking=delta.thinking)] if delta.type == "thinking_delta" else []
        if delta.type == "input_json_delta":
            if self._server is not None:
                self._server_input += delta.partial_json
                return []
            self._tool_input += delta.partial_json
            return [ProviderEvent(type="tool_use_delta", tool_input_delta=delta.partial_json)]
        return []

    def _on_content_block_stop(self, event: Any) -> list[ProviderEvent]:
        events: list[ProviderEvent] = []
        if self._thinking is not None:
            events.append(_provider_block(self._thinking))
            self._thinking = None
        if self._server is not None:
            events.append(_provider_block({**self._server, "input": _tool_input(self._server_input)}))
            self._server = None
        if self._tool:
            events.append(ProviderEvent(
                type="tool_use_complete",
                tool_use_id=self._tool["id"],
                tool_name=self._tool["name"],
                tool_input_complete=_tool_input(self._tool_input),
            ))
            self._tool, self._tool_input = {}, ""
        return events

    def _on_message_delta(self, event: Any) -> list[ProviderEvent]:
        if getattr(event, "usage", None):
            self.usage = {**self.usage, "output_tokens": _count(event.usage, "output_tokens")}
            # Newer API versions repeat the cumulative cache counters here.
            for key, name in (("cache_read_tokens", "cache_read_input_tokens"), ("cache_write_tokens", "cache_creation_input_tokens")):
                if reported := _count(event.usage, name):
                    self.usage[key] = reported
            if thinking := _thinking_tokens(event.usage):
                self.usage["thinking_tokens"] = thinking
        if hasattr(event, "delta") and hasattr(event.delta, "stop_reason"):
            self.stop_reason = event.delta.stop_reason or "end_turn"
        return []


class AnthropicProvider:
    """Anthropic Claude API provider."""

    name = ProviderName.ANTHROPIC
    supports_tools = True
    supports_streaming = True

    def __init__(self, config: ProviderConfig):
        self.config = config
        self._client: AsyncAnthropic | None = None
        self._effort: EffortTracker | None = None

    def _get_client(self) -> AsyncAnthropic:
        """Return cached AsyncAnthropic client (lazy-init)."""
        if self._client is None:
            from anthropic import AsyncAnthropic as _AsyncAnthropic

            client_kwargs: dict[str, Any] = {}
            if self.config.api_key:
                client_kwargs["api_key"] = self.config.api_key
            if self.config.base_url and self.config.base_url != "https://api.anthropic.com":
                client_kwargs["base_url"] = self.config.base_url
            self._client = _AsyncAnthropic(**client_kwargs)
        return self._client

    def _request_options(self) -> dict[str, Any]:
        """The thinking, token limit and sampling fields for this provider's configuration."""
        config = self.config
        return request_options(
            config.model, config.max_tokens, config.temperature,
            config.extended_thinking, config.thinking_budget, config.show_thinking,
        )

    def _tracker(self) -> EffortTracker:
        """The effort state of this conversation; built on first use so a bad ``reasoning_effort`` fails the request."""
        if self._effort is None:
            self._effort = EffortTracker(self.config.model, self.config.reasoning_effort)
        return self._effort

    def set_turn_effort(self, level: str) -> bool:
        """Run the following turns at effort *level* (``low`` to ``max``), keeping the prompt cache where the model allows.

        Models with per-message effort take the change from the next user message on. Any other model holds
        the level it started with, because changing the top-level value would restart the cache; so does a
        model that takes no effort. Returns False when the level is not applied. Raises ValueError for a
        level the model does not accept.
        """
        return self._tracker().set_turn(level)

    @property
    def supports_server_compaction(self) -> bool:
        """True when ``model.anthropic_compaction`` is on and the model takes the compaction request."""
        return self.config.anthropic_compaction == "on" and supports_compaction(self.config.model)

    def _prepare(
        self,
        system_prompt: str,
        messages: list[dict[str, Any]],
        tools: Sequence[ToolSpec],
        marks: dict[int, str] | None = None,
    ) -> tuple[Any, list[dict[str, Any]], list[dict[str, Any]]]:
        """System prompt, tool declarations and messages in the API's shape, cache breakpoints applied."""
        api_tools    = declare_tools(tools, self.config.model, self.config.anthropic_tool_search)
        api_messages = self._convert_messages(messages, marks)
        system: Any  = system_prompt
        if self.config.prompt_caching:
            system, api_tools, api_messages = with_cache_breakpoints(system_prompt, api_tools, api_messages)
        return system, api_tools, api_messages

    def _request(self, system_prompt: str, messages: list[dict[str, Any]], tools: Sequence[ToolSpec]) -> dict[str, Any]:
        """The keyword arguments of one messages call, everything except ``stream``.

        Effort and the beta headers sit outside the SDK's typed parameters on older SDK versions, so they go
        in ``extra_body`` and ``extra_headers``, which every SDK version that has them accepts.
        """
        effort, marks = self._tracker().prepare(messages)
        system, api_tools, api_messages = self._prepare(system_prompt, messages, tools, marks)
        kwargs: dict[str, Any] = {
            "model": self.config.model,
            **self._request_options(),
            "system": system,
            "messages": api_messages,
        }
        if api_tools:
            kwargs["tools"] = api_tools
        if effort:
            kwargs["extra_body"] = {"output_config": {"effort": effort}}
        if headers := beta_headers(api_messages):
            kwargs["extra_headers"] = headers
        return kwargs

    async def stream(
        self,
        system_prompt: str,
        messages: list[dict[str, Any]],
        tools: Sequence[ToolSpec],
    ) -> AsyncIterator[ProviderEvent]:
        """Stream completion from Anthropic API."""
        try:
            client = self._get_client()
        except ImportError:
            yield ProviderEvent(type="error", error="anthropic package not installed. Run: pip install anthropic")
            return

        try:
            kwargs = self._request(system_prompt, messages, tools)
            stream = await client.messages.create(**kwargs, stream=True)
            turn   = _Turn()
            async for event in stream:
                for out in turn.feed(event):
                    yield out

            # Emit usage and done
            if turn.usage.get("input_tokens") or turn.usage.get("output_tokens"):
                yield ProviderEvent(type="usage", usage=turn.usage)
            yield ProviderEvent(type="done", stop_reason=turn.stop_reason)

        except Exception as e:
            yield ProviderEvent(type="error", error=str(e), **error_fields(e))

    async def send(
        self,
        system_prompt: str,
        messages: list[dict[str, Any]],
        tools: Sequence[ToolSpec],
    ) -> dict[str, Any]:
        """Non-streaming completion."""
        try:
            client = self._get_client()
        except ImportError:
            return {"content": "anthropic package not installed", "is_error": True}

        try:
            response = await client.messages.create(**{"tools": [], **self._request(system_prompt, messages, tools)})

            content = ""
            tool_uses = []
            provider_blocks: list[dict[str, Any]] = []

            for block in response.content:
                if block.type == "thinking":
                    provider_blocks.append({"type": "thinking", "thinking": block.thinking, "signature": block.signature})
                elif block.type == "redacted_thinking":
                    provider_blocks.append(_redacted_block(block))
                elif block.type in ("server_tool_use", "tool_search_tool_result", "compaction"):
                    provider_blocks.append(plain(block))
                elif block.type == "text":
                    content += block.text
                elif block.type == "tool_use":
                    tool_uses.append(
                        {
                            "id": block.id,
                            "name": block.name,
                            "input": block.input,
                        }
                    )

            return {
                "content": content,
                "tool_uses": tool_uses,
                "stop_reason": response.stop_reason,
                "usage": _usage_dict(response.usage),
                **({"provider_blocks": provider_blocks} if provider_blocks else {}),
            }

        except Exception as e:
            return {"content": str(e), "is_error": True}

    async def compact(
        self,
        system_prompt: str,
        messages: list[dict[str, Any]],
        tools: Sequence[ToolSpec],
    ) -> dict[str, Any]:
        """Ask the API to summarize *messages* (on-demand compaction, beta).

        On success the result is ``{"provider_blocks": [block], "stop_reason": "compaction", "usage": ...}``
        with an empty ``content``. The caller appends it to the history as an assistant message that carries
        the block; from then on ``_convert_messages`` sends the block first and leaves out everything before
        it. A failure, or an API answer that holds no summary, is ``{"content": reason, "is_error": True}``
        and the conversation simply continues without one. Pass the same system prompt and tools as the
        ordinary requests: the summarizer reads them. Usage counts the summarizing call, which the API
        reports in ``usage.iterations``.
        """
        if not self.supports_server_compaction:
            return {"content": f"server-side compaction is off or not supported by {self.config.model}", "is_error": True}
        try:
            client  = self._get_client()
            kwargs  = self._request(system_prompt, messages, tools)
            kwargs["extra_body"]    = {**kwargs.get("extra_body", {}), "compaction": {"type": "summarize"}}
            kwargs["extra_headers"] = with_beta(kwargs.get("extra_headers", {}), BETA_COMPACTION)
            response = await client.messages.create(**kwargs, timeout=_COMPACTION_TIMEOUT)
        except ImportError:
            return {"content": "anthropic package not installed", "is_error": True}
        except Exception as e:
            return {"content": str(e), "is_error": True}
        blocks = [plain(block) for block in response.content if block.type == "compaction"]
        if response.stop_reason != "compaction" or not blocks:
            return {"content": f"the API returned no summary (stop_reason {response.stop_reason})", "is_error": True}
        usage = _usage_dict(response.usage)
        for key, tokens in zip(("input_tokens", "output_tokens"), _compaction_tokens(response.usage), strict=True):
            usage[key] += tokens
        return {"content": "", "tool_uses": [], "stop_reason": "compaction", "usage": usage, "provider_blocks": blocks}

    async def list_models(self) -> list[Any]:
        """Fetch available models from Anthropic API."""
        from nerdvana_cli.providers.base import ModelInfo
        try:
            client = self._get_client()
            response = await client.models.list()
            models = []
            for m in response.data:
                models.append(ModelInfo(
                    id=m.id,
                    name=getattr(m, 'display_name', m.id),
                    provider="anthropic",
                    created=str(getattr(m, 'created_at', '')),
                ))
            models.sort(key=lambda x: x.id)
            return models
        except Exception:
            return []

    @staticmethod
    def _assistant_blocks(msg: dict[str, Any]) -> list[dict[str, Any]]:
        """Content blocks of an assistant turn: what the API returned first and unchanged, then text, then tool calls."""
        content = msg.get("content", "")
        blocks: list[dict[str, Any]] = [dict(b) for b in msg.get("provider_blocks") or [] if b.get("type") in ECHOED_BLOCKS]
        if isinstance(content, str) and content.strip() and not (content == "[tool execution]" and msg.get("tool_uses")):
            blocks.append({"type": "text", "text": content})
        for tool_use in msg.get("tool_uses") or []:
            blocks.append({
                "type":  "tool_use",
                "id":    tool_use.get("id", ""),
                "name":  tool_use.get("name", ""),
                "input": tool_use.get("input") or {},
            })
        return blocks

    def _convert_messages(self, messages: list[dict[str, Any]], marks: dict[int, str] | None = None) -> list[dict[str, Any]]:
        """Convert internal messages to the Anthropic Messages API format.

        An assistant turn becomes the blocks the API returned for it (thinking, server-side tool search,
        compaction), a text block (only when non-empty), then one ``tool_use`` block per requested call.
        Tool results become ``tool_result`` blocks of a user message, and every adjacent run of
        user-side content (results, then injected notes) is merged into a single
        user message, which is what the API requires for parallel calls.

        *marks* maps the index of a message to an effort level that takes effect from it: an effort-only
        system message is placed before that message. A ``compaction`` block stands for everything before
        it, so those messages are left out and the effort in force at that point is stated again after it.
        """
        marks = marks or {}
        api_messages: list[dict[str, Any]] = []

        def _append(role: str, blocks: list[dict[str, Any]]) -> None:
            if not blocks:
                return
            if api_messages and api_messages[-1]["role"] == role:
                api_messages[-1]["content"].extend(blocks)
            else:
                api_messages.append({"role": role, "content": list(blocks)})

        for index, msg in enumerate(messages):
            role    = msg.get("role", "user")
            content = msg.get("content", "")
            if index in marks:
                api_messages.append(effort_message(marks[index]))

            if role == "tool":
                _append("user", [{
                    "type":        "tool_result",
                    "tool_use_id": msg.get("tool_use_id", ""),
                    "content":     content if isinstance(content, str) else str(content),
                    "is_error":    bool(msg.get("is_error", False)),
                }])
            elif role == "assistant":
                blocks = self._assistant_blocks(msg)
                if any(block["type"] == "compaction" for block in blocks):
                    api_messages.clear()
                    _append("assistant", blocks)
                    if earlier := [level for mark, level in sorted(marks.items()) if mark <= index]:
                        api_messages.append(effort_message(earlier[-1]))
                else:
                    _append("assistant", blocks)
            else:
                if isinstance(content, list):
                    _append("user", [_api_block(block) for block in content if isinstance(block, dict)])
                elif isinstance(content, str) and content.strip():
                    _append("user", [{"type": "text", "text": content}])
        return api_messages
