"""Google Gemini provider."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

from nerdvana_cli.core.tool import BaseTool
from nerdvana_cli.providers.base import ProviderConfig, ProviderEvent, ProviderName

try:
    from google.genai import Client as _GenaiClient
except ImportError:  # pragma: no cover – runtime guard in _get_client
    _GenaiClient = None  # type: ignore[assignment,misc]


class GeminiProvider:
    """Google Gemini API provider."""

    name = ProviderName.GEMINI
    supports_tools = True
    supports_streaming = True

    def __init__(self, config: ProviderConfig):
        self.config = config
        self._client: _GenaiClient | None = None

    def _get_client(self) -> _GenaiClient:
        """Return cached genai.Client (lazy-init)."""
        if self._client is None:
            from google import genai

            self._client = genai.Client(api_key=self.config.api_key) if self.config.api_key else genai.Client()
        return self._client

    async def stream(
        self,
        system_prompt: str,
        messages: list[dict[str, Any]],
        tools: list[BaseTool[Any]],
    ) -> AsyncIterator[ProviderEvent]:
        """Stream completion from Gemini API."""
        try:
            from google.genai import types
        except ImportError:
            yield ProviderEvent(
                type="error",
                error="google-genai not installed. Run: pip install google-genai",
            )
            return

        try:
            client = self._get_client()
        except ImportError:
            yield ProviderEvent(
                type="error",
                error="google-genai not installed. Run: pip install google-genai",
            )
            return

        # Convert tools to Gemini format
        gemini_tools = None
        if tools:
            function_declarations = [
                types.FunctionDeclaration(
                    name=t.name,
                    description=t.description_text,
                    parameters=self._convert_schema(t.input_schema),  # type: ignore[arg-type]
                )
                for t in tools
            ]
            gemini_tools = [types.Tool(function_declarations=function_declarations)]

        # Convert messages
        contents = self._convert_messages(messages)

        config = types.GenerateContentConfig(
            system_instruction=system_prompt,
            max_output_tokens=self.config.max_tokens,
            temperature=self.config.temperature,
            tools=gemini_tools,  # type: ignore[arg-type]
        )

        try:
            stream = await client.aio.models.generate_content_stream(
                model=self.config.model,
                contents=contents,
                config=config,
            )

            async for chunk in stream:
                if chunk.candidates:
                    for candidate in chunk.candidates:
                        if candidate.content and candidate.content.parts:
                            for part in candidate.content.parts:
                                if part.text:
                                    yield ProviderEvent(type="content_delta", content=part.text)
                                elif part.function_call:
                                    args = dict(part.function_call.args) if part.function_call.args else {}
                                    yield ProviderEvent(
                                        type="tool_use_complete",
                                    tool_name=part.function_call.name or "",
                                        tool_input_complete=args,
                                    )

            yield ProviderEvent(type="done", stop_reason="end_turn")

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
            from google.genai import types
            client = self._get_client()
        except ImportError:
            return {"content": "", "tool_uses": [], "usage": {}, "is_error": True}

        gemini_tools = None
        if tools:
            function_declarations = [
                types.FunctionDeclaration(
                    name=t.name,
                    description=t.description_text,
                    parameters=self._convert_schema(t.input_schema),  # type: ignore[arg-type]
                )
                for t in tools
            ]
            gemini_tools = [types.Tool(function_declarations=function_declarations)]

        contents = self._convert_messages(messages)
        config = types.GenerateContentConfig(
            system_instruction=system_prompt,
            max_output_tokens=self.config.max_tokens,
            temperature=self.config.temperature,
            tools=gemini_tools,  # type: ignore[arg-type]
        )

        try:
            response = await client.aio.models.generate_content(
                model=self.config.model,
                contents=contents,
                config=config,
            )

            content = ""
            tool_uses = []

            if response.candidates:
                for candidate in response.candidates:
                    if candidate.content and candidate.content.parts:
                        for part in candidate.content.parts:
                            if part.text:
                                content += part.text
                            elif part.function_call:
                                args = dict(part.function_call.args) if part.function_call.args else {}
                                tool_uses.append(
                                    {
                                        "id": f"call_{part.function_call.name}_{uuid.uuid4().hex[:8]}",
                                        "name": part.function_call.name,
                                        "input": args,
                                    }
                                )

            usage = {}
            if response.usage_metadata:
                usage = {
                    "input_tokens": response.usage_metadata.prompt_token_count or 0,
                    "output_tokens": response.usage_metadata.candidates_token_count or 0,
                }

            return {
                "content": content,
                "tool_uses": tool_uses,
                "stop_reason": "tool_use" if tool_uses else "end_turn",
                "usage": usage,
            }

        except Exception as e:
            return {"content": str(e), "tool_uses": [], "usage": {}, "is_error": True}

    async def list_models(self) -> list[Any]:
        """Fetch available models from Gemini API."""
        from nerdvana_cli.providers.base import ModelInfo
        try:
            client = self._get_client()
            models: list[Any] = []
            for m in client.models.list():
                m_name: str = m.name or ""
                model_id = m_name.replace("models/", "") if m_name.startswith("models/") else m_name
                models.append(ModelInfo(
                    id=model_id,
                    name=getattr(m, 'display_name', model_id) or model_id,
                    provider="gemini",
                ))
            models.sort(key=lambda x: x.id)
            return models
        except Exception:
            return []

    def _convert_messages(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Convert messages to Gemini format."""
        contents = []

        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")

            if role == "tool":
                tool_id = msg.get("tool_use_id", "")
                tool_name = tool_id.split(":")[0] if ":" in tool_id else tool_id
                contents.append(
                    {
                        "role": "user",
                        "parts": [
                            {
                                "functionResponse": {
                                    "name": tool_name,
                                    "response": {"result": content, "is_error": msg.get("is_error", False)},
                                }
                            }
                        ],
                    }
                )
            elif role == "assistant":
                parts: list[dict[str, Any]] = []
                if isinstance(content, str) and content:
                    parts.append({"text": content})
                if isinstance(content, list):
                    for item in content:
                        if item.get("type") == "tool_use":
                            parts.append(
                                {
                                    "functionCall": {
                                        "name": item["name"],
                                        "args": item.get("input", {}),
                                    }
                                }
                            )
                        elif item.get("type") == "text":
                            parts.append({"text": item["text"]})

                contents.append({"role": "model", "parts": parts if parts else [{"text": ""}]})
            elif role == "user":
                if isinstance(content, list):
                    parts_u: list[dict[str, Any]] = []
                    for item in content:
                        if item.get("type") == "tool_result":
                            parts_u.append(
                                {
                                    "functionResponse": {
                                        "name": item.get("name", "unknown"),
                                        "response": {"result": item.get("content", "")},
                                    }
                                }
                            )
                        elif item.get("type") == "text":
                            parts_u.append({"text": item["text"]})
                    contents.append({"role": "user", "parts": parts_u})
                else:
                    contents.append({"role": "user", "parts": [{"text": str(content)}]})
            else:
                contents.append({"role": "user", "parts": [{"text": str(content)}]})

        return contents

    def _convert_schema(self, schema: dict[str, Any]) -> dict[str, Any]:
        """Convert OpenAI-style JSON schema to Gemini format (recursive)."""
        if not schema:
            return {}
        raw_type = schema.get("type", "OBJECT")
        type_map = {"integer": "INTEGER", "number": "NUMBER", "boolean": "BOOLEAN",
                     "array": "ARRAY", "object": "OBJECT", "string": "STRING"}
        result: dict[str, Any] = {"type": type_map.get(raw_type.lower(), "STRING")}
        if "properties" in schema:
            result["properties"] = {
                name: self._convert_schema(prop) for name, prop in schema["properties"].items()
            }
        if "items" in schema:
            result["items"] = self._convert_schema(schema["items"])
        if "required" in schema:
            result["required"] = schema["required"]
        if "description" in schema:
            result["description"] = schema["description"]
        if "enum" in schema:
            result["enum"] = schema["enum"]
        return result
