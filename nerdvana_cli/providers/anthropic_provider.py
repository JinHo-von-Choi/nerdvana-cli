"""Anthropic Claude provider — native API integration."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

from rich.console import Console

from nerdvana_cli.core.tool import BaseTool
from nerdvana_cli.providers.base import ProviderConfig, ProviderEvent, ProviderName

try:
    from anthropic import AsyncAnthropic
except ImportError:  # pragma: no cover – runtime guard in _get_client
    AsyncAnthropic = None  # type: ignore[assignment,misc]

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
        tools: list[BaseTool[Any]],
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

        try:
            create_kwargs: dict[str, Any] = {
                "model": self.config.model,
                "max_tokens": self.config.max_tokens,
                "temperature": self.config.temperature,
                "system": system_prompt,
                "messages": api_messages,
                "stream": True,
            }
            if api_tools:
                create_kwargs["tools"] = api_tools

            stream = await client.messages.create(**create_kwargs)

            # Track tool blocks for completion events
            current_tool: dict[str, Any] = {}
            current_tool_input = ""
            input_tokens = 0
            output_tokens = 0
            stop_reason = "end_turn"

            async for event in stream:
                if event.type == "message_start":
                    msg = getattr(event, "message", None)
                    if msg and hasattr(msg, "usage") and msg.usage:
                        input_tokens = getattr(msg.usage, "input_tokens", 0) or 0

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
                        output_tokens = getattr(event.usage, "output_tokens", 0) or 0
                    if hasattr(event, "delta") and hasattr(event.delta, "stop_reason"):
                        stop_reason = event.delta.stop_reason or "end_turn"

            # Emit usage and done
            if input_tokens or output_tokens:
                yield ProviderEvent(
                    type="usage",
                    usage={"input_tokens": input_tokens, "output_tokens": output_tokens},
                )
            yield ProviderEvent(type="done", stop_reason=stop_reason)

        except Exception as e:
            yield ProviderEvent(type="error", error=str(e))

    async def send(
        self,
        system_prompt: str,
        messages: list[dict[str, Any]],
        tools: list[BaseTool[Any]],
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

        try:
            response = await client.messages.create(
                model=self.config.model,
                max_tokens=self.config.max_tokens,
                temperature=self.config.temperature,
                system=system_prompt,
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
                "usage": {
                    "input_tokens": response.usage.input_tokens,
                    "output_tokens": response.usage.output_tokens,
                },
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
        """Convert internal messages to Anthropic API format."""
        api_messages = []
        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")

            if role == "tool":
                api_messages.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": msg.get("tool_use_id", ""),
                                "content": content,
                                "is_error": msg.get("is_error", False),
                            }
                        ],
                    }
                )
            else:
                api_messages.append({"role": role, "content": content})
        return api_messages
