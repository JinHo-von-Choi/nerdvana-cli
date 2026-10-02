"""Anthropic Claude provider — native API integration."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Sequence
from typing import Any

from rich.console import Console

from nerdvana_cli.providers.base import ProviderConfig, ProviderEvent, ProviderName
from nerdvana_cli.providers.errors import error_fields
from nerdvana_cli.types import ToolSpec

try:
    from anthropic import AsyncAnthropic
except ImportError:  # pragma: no cover – runtime guard in _get_client
    AsyncAnthropic = None  # type: ignore[assignment,misc]

# Block types that may carry a cache breakpoint.
_CACHEABLE_BLOCKS = frozenset({"text", "tool_use", "tool_result", "image"})
_EPHEMERAL: dict[str, str] = {"type": "ephemeral"}


def _count(obj: Any, name: str) -> int:
    """An integer usage counter from *obj*, 0 when absent or not a number."""
    value = getattr(obj, name, 0)
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _usage_dict(usage: Any, output_tokens: int | None = None) -> dict[str, int]:
    """Normalise an Anthropic usage object.

    ``input_tokens`` of the result is the whole prompt: Anthropic reports only the
    tokens after the last cache breakpoint there, with cache writes and reads
    counted separately.
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
    return result


def with_cache_breakpoints(
    system_prompt: str,
    api_tools:     list[dict[str, Any]],
    api_messages:  list[dict[str, Any]],
) -> tuple[Any, list[dict[str, Any]], list[dict[str, Any]]]:
    """Mark the prompt prefix for caching; returns (system, tools, messages).

    The prefix is built tools, then system, then messages, so breakpoints go on the
    last tool, the system prompt, and the last block of the conversation. That is
    three of the four allowed. Inputs are not modified.
    """
    tools = [dict(tool) for tool in api_tools]
    if tools:
        tools[-1]["cache_control"] = dict(_EPHEMERAL)

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


class AnthropicProvider:
    """Anthropic Claude API provider."""

    name = ProviderName.ANTHROPIC
    supports_tools = True
    supports_streaming = True

    def __init__(self, config: ProviderConfig):
        self.config = config
        self._client: AsyncAnthropic | None = None

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

        api_tools = [
            {
                "name": t.name,
                "description": t.description_text,
                "input_schema": t.input_schema,
            }
            for t in tools
        ]

        api_messages = self._convert_messages(messages)
        system: Any  = system_prompt
        if self.config.prompt_caching:
            system, api_tools, api_messages = with_cache_breakpoints(system_prompt, api_tools, api_messages)

        try:
            create_kwargs: dict[str, Any] = {
                "model": self.config.model,
                "max_tokens": self.config.max_tokens,
                "temperature": self.config.temperature,
                "system": system,
                "messages": api_messages,
                "stream": True,
            }
            if api_tools:
                create_kwargs["tools"] = api_tools

            stream = await client.messages.create(**create_kwargs)

            # Track tool blocks for completion events
            current_tool: dict[str, Any] = {}
            current_tool_input = ""
            usage: dict[str, int] = {}
            stop_reason = "end_turn"

            async for event in stream:
                if event.type == "message_start":
                    msg = getattr(event, "message", None)
                    if msg and hasattr(msg, "usage") and msg.usage:
                        usage = _usage_dict(msg.usage)

                elif event.type == "content_block_start":
                    cb = event.content_block
                    if cb.type == "tool_use":
                        current_tool = {"id": cb.id, "name": cb.name}
                        current_tool_input = ""
                        yield ProviderEvent(
                            type="tool_use_start",
                            tool_use_id=cb.id,
                            tool_name=cb.name,
                        )

                elif event.type == "content_block_delta":
                    delta = event.delta
                    if delta.type == "text_delta":
                        yield ProviderEvent(type="content_delta", content=delta.text)
                    elif delta.type == "thinking_delta":
                        yield ProviderEvent(type="thinking_delta", thinking=delta.thinking)
                    elif delta.type == "input_json_delta":
                        current_tool_input += delta.partial_json
                        yield ProviderEvent(
                            type="tool_use_delta",
                            tool_input_delta=delta.partial_json,
                        )

                elif event.type == "content_block_stop":
                    if current_tool:
                        try:
                            input_data = json.loads(current_tool_input) if current_tool_input else {}
                        except json.JSONDecodeError:
                            input_data = {}
                        yield ProviderEvent(
                            type="tool_use_complete",
                            tool_use_id=current_tool["id"],
                            tool_name=current_tool["name"],
                            tool_input_complete=input_data,
                        )
                        current_tool = {}
                        current_tool_input = ""

                elif event.type == "message_delta":
                    if hasattr(event, "usage") and event.usage:
                        usage = {**usage, "output_tokens": _count(event.usage, "output_tokens")}
                        # Newer API versions repeat the cumulative cache counters here.
                        for key, name in (("cache_read_tokens", "cache_read_input_tokens"), ("cache_write_tokens", "cache_creation_input_tokens")):
                            reported = _count(event.usage, name)
                            if reported:
                                usage[key] = reported
                    if hasattr(event, "delta") and hasattr(event.delta, "stop_reason"):
                        stop_reason = event.delta.stop_reason or "end_turn"

            # Emit usage and done
            if usage.get("input_tokens") or usage.get("output_tokens"):
                yield ProviderEvent(type="usage", usage=usage)
            yield ProviderEvent(type="done", stop_reason=stop_reason)

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

        api_tools = [
            {
                "name": t.name,
                "description": t.description_text,
                "input_schema": t.input_schema,
            }
            for t in tools
        ]

        api_messages = self._convert_messages(messages)
        system: Any  = system_prompt
        if self.config.prompt_caching:
            system, api_tools, api_messages = with_cache_breakpoints(system_prompt, api_tools, api_messages)

        try:
            response = await client.messages.create(
                model=self.config.model,
                max_tokens=self.config.max_tokens,
                temperature=self.config.temperature,
                system=system,
                messages=api_messages,  # type: ignore[arg-type]
                tools=api_tools,  # type: ignore[arg-type]
            )

            content = ""
            tool_uses = []

            for block in response.content:
                if block.type == "text":
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
            }

        except Exception as e:
            return {"content": str(e), "is_error": True}

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

    def _convert_messages(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Convert internal messages to the Anthropic Messages API format.

        An assistant turn becomes a text block (only when non-empty) followed by
        one ``tool_use`` block per requested call. Tool results become
        ``tool_result`` blocks of a user message, and every adjacent run of
        user-side content (results, then injected notes) is merged into a single
        user message, which is what the API requires for parallel calls.
        """
        api_messages: list[dict[str, Any]] = []

        def _append(role: str, blocks: list[dict[str, Any]]) -> None:
            if not blocks:
                return
            if api_messages and api_messages[-1]["role"] == role:
                api_messages[-1]["content"].extend(blocks)
            else:
                api_messages.append({"role": role, "content": list(blocks)})

        for msg in messages:
            role    = msg.get("role", "user")
            content = msg.get("content", "")

            if role == "tool":
                _append("user", [{
                    "type":        "tool_result",
                    "tool_use_id": msg.get("tool_use_id", ""),
                    "content":     content if isinstance(content, str) else str(content),
                    "is_error":    bool(msg.get("is_error", False)),
                }])
            elif role == "assistant":
                blocks: list[dict[str, Any]] = []
                if isinstance(content, str) and content.strip():
                    blocks.append({"type": "text", "text": content})
                for tool_use in msg.get("tool_uses") or []:
                    blocks.append({
                        "type":  "tool_use",
                        "id":    tool_use.get("id", ""),
                        "name":  tool_use.get("name", ""),
                        "input": tool_use.get("input") or {},
                    })
                _append("assistant", blocks)
            else:
                if isinstance(content, list):
                    _append("user", [dict(block) for block in content if isinstance(block, dict)])
                elif isinstance(content, str) and content.strip():
                    _append("user", [{"type": "text", "text": content}])
        return api_messages
