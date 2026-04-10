"""OpenAI-compatible provider — covers OpenAI, Groq, OpenRouter, xAI, Ollama, vLLM, DeepSeek, Mistral, Together."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

from rich.console import Console

from nerdvana_cli.core.tool import BaseTool
from nerdvana_cli.providers.base import ProviderConfig, ProviderEvent, ProviderName

console = Console()


def _safe_str(value: Any) -> str:
    """Safely convert any value to string, handling encoding errors."""
    if value is None:
        return ""
    try:
        return str(value)
    except Exception:
        return ""


class OpenAIProvider:
    """OpenAI-compatible API provider. Works with any OpenAI-compatible endpoint."""

    name = ProviderName.OPENAI
    supports_tools = True
    supports_streaming = True

    def __init__(self, config: ProviderConfig):
        self.config = config
        self._client = None

    def _get_client(self):
        """Return cached AsyncOpenAI client (lazy-init)."""
        if self._client is None:
            from openai import AsyncOpenAI

            kwargs: dict[str, Any] = {}
            if self.config.api_key:
                kwargs["api_key"] = self.config.api_key
            if self.config.base_url:
                kwargs["base_url"] = self.config.base_url
            self._client = AsyncOpenAI(**kwargs)
        return self._client

    async def list_models(self) -> list:
        """Fetch available models from the API."""
        from nerdvana_cli.providers.base import ModelInfo

        try:
            client = self._get_client()
            response = await client.models.list()
            models = []
            for m in response.data:
                models.append(
                    ModelInfo(
                        id=m.id,
                        provider=self.config.provider.value,
                        created=str(getattr(m, "created", "")),
                    )
                )
            models.sort(key=lambda x: x.id)
            return models
        except Exception:
            return []

    async def stream(
        self,
        system_prompt: str,
        messages: list[dict[str, Any]],
        tools: list[BaseTool],
    ) -> AsyncIterator[ProviderEvent]:
        """Stream completion from OpenAI-compatible API with UTF-8 safety."""
        try:
            client = self._get_client()
        except ImportError:
            yield ProviderEvent(type="error", error="openai package not installed. Run: pip install openai")
            return

        api_tools = self._build_tools(tools)
        api_messages = self._convert_messages(system_prompt, messages)

        try:
            # Some providers don't support stream_options
            create_kwargs: dict[str, Any] = {
                "model": self.config.model,
                "max_tokens": self.config.max_tokens,
                "temperature": self.config.temperature,
                "messages": api_messages,
                "tools": api_tools,
                "stream": True,
            }
            try:
                stream = await client.chat.completions.create(
                    **create_kwargs,
                    stream_options={"include_usage": True},
                )
            except Exception:
                # Fallback without stream_options
                stream = await client.chat.completions.create(**create_kwargs)

            current_tool_calls: dict[int, dict[str, str]] = {}
            usage_received = False
            total_completion_chars = 0

            async for chunk in stream:
                try:
                    if not chunk.choices:
                        if chunk.usage:
                            usage_received = True
                            yield ProviderEvent(
                                type="usage",
                                usage={
                                    "input_tokens": getattr(chunk.usage, "prompt_tokens", 0) or 0,
                                    "output_tokens": getattr(chunk.usage, "completion_tokens", 0) or 0,
                                },
                            )
                        continue

                    choice = chunk.choices[0]

                    # Content delta — handle potential encoding issues
                    if choice.delta.content:
                        total_completion_chars += len(choice.delta.content)
                        yield ProviderEvent(type="content_delta", content=choice.delta.content)

                    # Tool call deltas
                    if choice.delta.tool_calls:
                        for tc in choice.delta.tool_calls:
                            idx = tc.index
                            if idx not in current_tool_calls:
                                current_tool_calls[idx] = {
                                    "id": tc.id or "",
                                    "name": _safe_str(tc.function.name if tc.function else ""),
                                    "arguments": "",
                                }

                            if tc.function and tc.function.arguments:
                                current_tool_calls[idx]["arguments"] += _safe_str(tc.function.arguments)

                    # Finish
                    if choice.finish_reason:
                        stop_reason = (
                            "end_turn"
                            if choice.finish_reason == "stop"
                            else ("tool_use" if choice.finish_reason == "tool_calls" else choice.finish_reason)
                        )

                        if choice.finish_reason == "tool_calls":
                            for _idx, tc in current_tool_calls.items():
                                try:
                                    input_data = json.loads(tc["arguments"]) if tc["arguments"] else {}
                                except (json.JSONDecodeError, UnicodeDecodeError):
                                    input_data = {}

                                yield ProviderEvent(
                                    type="tool_use_complete",
                                    tool_use_id=tc["id"],
                                    tool_name=tc["name"],
                                    tool_input_complete=input_data,
                                )

                        # Emit estimated usage if no usage event received
                        if not usage_received and total_completion_chars > 0:
                            yield ProviderEvent(
                                type="usage",
                                usage={
                                    "input_tokens": len(str(api_messages)) // 4,
                                    "output_tokens": total_completion_chars // 4,
                                },
                            )

                        yield ProviderEvent(type="done", stop_reason=stop_reason)

                except UnicodeDecodeError:
                    # Skip chunks with encoding issues — next chunk will be fine
                    continue

        except UnicodeDecodeError as e:
            yield ProviderEvent(
                type="error",
                error=f"UTF-8 decoding error from API: {e}. Try a different model or provider.",
            )
        except Exception as e:
            error_str = str(e)
            if "utf-8" in error_str.lower() or "decode" in error_str.lower():
                yield ProviderEvent(
                    type="error",
                    error=f"Encoding error from API: {error_str}. This may be a model-specific issue.",
                )
            else:
                yield ProviderEvent(type="error", error=error_str)

    async def send(
        self,
        system_prompt: str,
        messages: list[dict[str, Any]],
        tools: list[BaseTool],
    ) -> dict[str, Any]:
        """Non-streaming completion."""
        try:
            client = self._get_client()
        except ImportError:
            return {"content": "openai package not installed", "is_error": True}

        api_tools = self._build_tools(tools)
        api_messages = self._convert_messages(system_prompt, messages)

        try:
            response = await client.chat.completions.create(
                model=self.config.model,
                max_tokens=self.config.max_tokens,
                temperature=self.config.temperature,
                messages=api_messages,
                tools=api_tools,
            )

            content = ""
            tool_uses = []

            for choice in response.choices:
                if choice.message.content:
                    content += choice.message.content

                if choice.message.tool_calls:
                    for tc in choice.message.tool_calls:
                        try:
                            input_data = json.loads(tc.function.arguments) if tc.function.arguments else {}
                        except (json.JSONDecodeError, UnicodeDecodeError):
                            input_data = {}

                        tool_uses.append(
                            {
                                "id": tc.id,
                                "name": tc.function.name,
                                "input": input_data,
                            }
                        )

            usage = {}
            if response.usage:
                usage = {
                    "input_tokens": response.usage.prompt_tokens,
                    "output_tokens": response.usage.completion_tokens,
                }

            return {
                "content": content,
                "tool_uses": tool_uses,
                "stop_reason": response.choices[0].finish_reason if response.choices else "stop",
                "usage": usage,
            }

        except UnicodeDecodeError as e:
            return {"content": f"UTF-8 decoding error: {e}", "is_error": True}
        except Exception as e:
            return {"content": str(e), "is_error": True}

    def _build_tools(self, tools: list[BaseTool]) -> list[dict[str, Any]]:
        """Build tool definitions for API call."""
        return [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description_text,
                    "parameters": t.input_schema,
                },
            }
            for t in tools
        ]

    def _convert_messages(self, system_prompt: str, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Convert internal messages to OpenAI API format with safe encoding."""
        api_messages: list[dict[str, Any]] = []

        # System message
        if system_prompt:
            api_messages.append({"role": "system", "content": system_prompt})

        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")

            # Ensure content is a safe string
            if not isinstance(content, str):
                content = _safe_str(content)

            if role == "tool":
                tool_id = _safe_str(msg.get("tool_use_id", ""))
                api_messages.append(
                    {
                        "role": "tool",
                        "content": content,
                        "tool_call_id": tool_id,
                    }
                )
            elif role == "assistant" and msg.get("tool_uses"):
                tool_calls: list[dict[str, Any]] = []
                for tu in msg.get("tool_uses", []):
                    try:
                        args_str = json.dumps(tu.get("input", {}), ensure_ascii=False)
                    except Exception:
                        args_str = "{}"
                    tool_calls.append(
                        {
                            "id": _safe_str(tu.get("id", "")),
                            "type": "function",
                            "function": {
                                "name": _safe_str(tu.get("name", "")),
                                "arguments": args_str,
                            },
                        }
                    )
                api_messages.append(
                    {
                        "role": "assistant",
                        "content": content or None,
                        "tool_calls": tool_calls,
                    }
                )
            else:
                api_messages.append({"role": role, "content": content})

        return api_messages
