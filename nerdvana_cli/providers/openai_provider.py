"""OpenAI-compatible provider — covers OpenAI, Groq, OpenRouter, xAI, Ollama, vLLM, DeepSeek, Mistral, Together."""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator, Iterator, Sequence
from typing import Any

from rich.console import Console

from nerdvana_cli.providers.base import DEFAULT_BASE_URLS, ProviderConfig, ProviderEvent, ProviderName
from nerdvana_cli.providers.errors import DECODE, error_fields
from nerdvana_cli.providers.thinking_parser import ThinkBlockParser
from nerdvana_cli.types import ToolSpec

try:
    from openai import AsyncOpenAI
except ImportError:  # pragma: no cover – runtime guard in _get_client
    AsyncOpenAI = None  # type: ignore[assignment,misc]

console = Console()
logger  = logging.getLogger(__name__)


_UNSUPPORTED_PARAM_STATUS = frozenset({400, 422})

_EFFORT_WITH_TOOLS_NOTE = (
    "reasoning_effort {effort!r} is sent with tools to the OpenAI Chat Completions endpoint, which supports "
    "tool calling only with reasoning_effort 'none' on GPT-5.4 and later; set model.reasoning_effort to 'none'"
)


def is_official_openai(config: ProviderConfig) -> bool:
    """Report whether *config* addresses OpenAI itself rather than another OpenAI-compatible server."""
    if config.provider != ProviderName.OPENAI:
        return False
    return config.base_url.rstrip("/") in ("", DEFAULT_BASE_URLS[ProviderName.OPENAI])


def _is_stream_options_unsupported(exc: BaseException) -> bool:
    """Report whether an endpoint rejected the request over ``stream_options``.

    Only two shapes mean the parameter is unsupported: the client refusing the
    keyword outright (``TypeError``) and the endpoint rejecting the request
    body, which the openai SDK raises as ``BadRequestError`` (400) or
    ``UnprocessableEntityError`` (422). The status is read off the exception so
    any OpenAI-compatible SDK is covered. Authentication, rate limit and server
    failures are excluded on purpose: resending those bills a second request
    and buries the status that actually needs to reach the caller.
    """
    if isinstance(exc, TypeError):
        return True
    return getattr(exc, "status_code", None) in _UNSUPPORTED_PARAM_STATUS


def _int(value: Any) -> int:
    """*value* as a non-negative int, 0 for anything that is not a number."""
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 0


def _usage_dict(usage: Any) -> dict[str, int]:
    """Normalise an OpenAI-style usage object.

    ``input_tokens`` is the whole prompt (cached tokens included); the cached part
    is reported separately as ``cache_read_tokens`` when the server says so.
    """
    cached = _int(getattr(getattr(usage, "prompt_tokens_details", None), "cached_tokens", 0))
    cached = cached or _int(getattr(usage, "prompt_cache_hit_tokens", 0))  # DeepSeek
    result = {
        "input_tokens":  _int(getattr(usage, "prompt_tokens", 0)),
        "output_tokens": _int(getattr(usage, "completion_tokens", 0)),
    }
    if cached:
        result["cache_read_tokens"] = cached
    return result


def _stop_reason(finish_reason: str | None, has_tool_calls: bool) -> str:
    """Map an OpenAI-style ``finish_reason`` to the loop's stop reasons.

    Some servers report ``stop`` (or nothing) on a response that carries tool
    calls, so the calls themselves decide: a response with calls is a tool_use.
    """
    if has_tool_calls and finish_reason in (None, "stop", "tool_calls", "function_call"):
        return "tool_use"
    if finish_reason in (None, "stop", "tool_calls", "function_call"):
        return "end_turn"
    if finish_reason == "length":
        return "max_tokens"
    return finish_reason or "end_turn"


def _content_parts(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Text and image blocks as chat-completions content parts (images as data URLs)."""
    parts: list[dict[str, Any]] = []
    for block in blocks:
        if block.get("type") == "image":
            parts.append({"type": "image_url", "image_url": {"url": f"data:{block['media_type']};base64,{block['data']}"}})
        elif block.get("type") == "text":
            parts.append({"type": "text", "text": block.get("text", "")})
    return parts


def _collect_tool_call(tc: Any, slots: list[dict[str, str]], slot_by_index: dict[int | None, dict[str, str]]) -> None:
    """Fold one streamed tool call delta into its slot.

    A provider may reuse an index for a different call, so a new id on a used index opens a new slot.
    """
    call_id = tc.id or ""
    slot    = slot_by_index.get(tc.index)
    if slot is not None and call_id and slot["id"] and call_id != slot["id"]:
        slot = None
    if slot is None:
        slot = {"id": call_id, "name": "", "arguments": ""}
        slots.append(slot)
        slot_by_index[tc.index] = slot
    if call_id and not slot["id"]:
        slot["id"] = call_id
    if tc.function:
        if tc.function.name and not slot["name"]:
            slot["name"] = _safe_str(tc.function.name)
        if tc.function.arguments:
            slot["arguments"] += _safe_str(tc.function.arguments)


def parse_arguments(raw: str | None) -> dict[str, Any]:
    """The JSON object in a tool call's *raw* arguments; empty or unparseable arguments give an empty input."""
    try:
        return json.loads(raw) if raw else {}
    except (json.JSONDecodeError, UnicodeDecodeError):
        return {}


def _tool_use_events(slots: list[dict[str, str]]) -> Iterator[ProviderEvent]:
    """One ``tool_use_complete`` event per collected call."""
    for slot in slots:
        yield ProviderEvent(
            type="tool_use_complete",
            tool_use_id=slot["id"],
            tool_name=slot["name"],
            tool_input_complete=parse_arguments(slot["arguments"]),
        )


def _with_note(text: str, exc: BaseException, note: str) -> str:
    """*text* followed by *note* when the endpoint refused the request body, the case the note explains."""
    if note and getattr(exc, "status_code", None) in _UNSUPPORTED_PARAM_STATUS:
        return f"{text} ({note})"
    return text


def _error_event(exc: Exception, note: str = "") -> ProviderEvent:
    """The error event for a failed request, with the encoding failures told apart."""
    text = str(exc)
    if isinstance(exc, UnicodeDecodeError):
        return ProviderEvent(
            type="error",
            error=f"UTF-8 decoding error from API: {exc}. Try a different model or provider.",
            **error_fields(exc),
        )
    if "utf-8" in text.lower() or "decode" in text.lower():
        return ProviderEvent(
            type="error",
            error=f"Encoding error from API: {text}. This may be a model-specific issue.",
            **error_fields(exc, kind=DECODE),
        )
    return ProviderEvent(type="error", error=_with_note(text, exc, note), **error_fields(exc))


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
        self._client: AsyncOpenAI | None = None
        self._effort_warned = False

    def _get_client(self) -> AsyncOpenAI:
        """Return cached AsyncOpenAI client (lazy-init)."""
        if self._client is None:
            from openai import AsyncOpenAI as _AsyncOpenAI

            kwargs: dict[str, Any] = {}
            if self.config.api_key:
                kwargs["api_key"] = self.config.api_key
            if self.config.base_url:
                kwargs["base_url"] = self.config.base_url
            self._client = _AsyncOpenAI(**kwargs)
        return self._client

    async def list_models(self) -> list[Any]:
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
        tools: Sequence[ToolSpec],
    ) -> AsyncIterator[ProviderEvent]:
        """Stream completion from OpenAI-compatible API with UTF-8 safety."""
        try:
            client = self._get_client()
        except ImportError:
            yield ProviderEvent(type="error", error="openai package not installed. Run: pip install openai")
            return

        api_tools = self._build_tools(tools)
        api_messages = self._convert_messages(system_prompt, messages)
        effort_note = self._effort_note(bool(api_tools))

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
            create_kwargs.update(self._effort_kwargs())
            try:
                stream = await client.chat.completions.create(
                    **create_kwargs,
                    stream_options={"include_usage": True},
                )
            except Exception as exc:
                if not _is_stream_options_unsupported(exc):
                    raise
                # Fallback without stream_options
                stream = await client.chat.completions.create(**create_kwargs)

            # One slot per tool call, in arrival order. A provider may reuse an
            # index for a different call, so a new id on a used index opens a new slot.
            slots:         list[dict[str, str]]         = []
            slot_by_index: dict[int | None, dict[str, str]] = {}
            finish_reason: str | None                   = None
            reported:      Any                          = None
            total_completion_chars = 0
            parser = ThinkBlockParser()

            async for chunk in stream:
                try:
                    # Servers put the usage on a chunk of its own (OpenAI) or on the last chunk that also
                    # carries the finish reason (others), and some repeat it on every chunk. The last
                    # report that says anything is the total, and it is emitted once, after the stream.
                    if getattr(chunk, "usage", None) and _usage_dict(chunk.usage)["input_tokens"] + _usage_dict(chunk.usage)["output_tokens"] > 0:
                        reported = chunk.usage
                    if not chunk.choices:
                        continue

                    choice = chunk.choices[0]

                    # Content delta — handle potential encoding issues
                    if choice.delta.content:
                        total_completion_chars += len(choice.delta.content)
                        parsed = parser.feed(choice.delta.content)
                        if parsed.content:
                            yield ProviderEvent(type="content_delta", content=parsed.content)
                        if parsed.thinking:
                            yield ProviderEvent(type="thinking_delta", thinking=parsed.thinking)

                    # Tool call deltas
                    for tc in choice.delta.tool_calls or ():
                        _collect_tool_call(tc, slots, slot_by_index)

                    # The first finish reason wins; later chunks may repeat it.
                    if choice.finish_reason and finish_reason is None:
                        finish_reason = choice.finish_reason

                except UnicodeDecodeError:
                    # Skip chunks with encoding issues — next chunk will be fine
                    continue

            # The response is complete: emit what it contained exactly once.
            final = parser.flush()
            if final.content:
                yield ProviderEvent(type="content_delta", content=final.content)
            if final.thinking:
                yield ProviderEvent(type="thinking_delta", thinking=final.thinking)

            stop_reason = _stop_reason(finish_reason, bool(slots))
            if stop_reason == "tool_use":
                for event in _tool_use_events(slots):
                    yield event

            if reported is not None:
                yield ProviderEvent(type="usage", usage=_usage_dict(reported))
            elif total_completion_chars > 0:
                # The server reported nothing: estimate from the characters sent and received.
                yield ProviderEvent(
                    type="usage",
                    usage={
                        "input_tokens": len(str(api_messages) + str(api_tools)) // 4,
                        "output_tokens": total_completion_chars // 4,
                    },
                )

            yield ProviderEvent(type="done", stop_reason=stop_reason)

        except Exception as e:
            yield _error_event(e, effort_note)

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
            return {"content": "openai package not installed", "is_error": True}

        api_tools = self._build_tools(tools)
        api_messages = self._convert_messages(system_prompt, messages)
        effort_note = self._effort_note(bool(api_tools))

        try:
            response = await client.chat.completions.create(
                model=self.config.model,
                max_tokens=self.config.max_tokens,
                temperature=self.config.temperature,
                messages=api_messages,  # type: ignore[arg-type]
                tools=api_tools,  # type: ignore[arg-type]
                **self._effort_kwargs(),
            )

            content = ""
            tool_uses = []

            for choice in response.choices:
                if choice.message.content:
                    content += choice.message.content

                if choice.message.tool_calls:
                    for tc in choice.message.tool_calls:
                        tool_uses.append(
                            {
                                "id": tc.id,
                                "name": tc.function.name,  # type: ignore[union-attr]
                                "input": parse_arguments(tc.function.arguments),  # type: ignore[union-attr]
                            }
                        )

            usage = _usage_dict(response.usage) if response.usage else {}

            return {
                "content": content,
                "tool_uses": tool_uses,
                "stop_reason": response.choices[0].finish_reason if response.choices else "stop",
                "usage": usage,
            }

        except UnicodeDecodeError as e:
            return {"content": f"UTF-8 decoding error: {e}", "is_error": True}
        except Exception as e:
            return {"content": _with_note(str(e), e, effort_note), "is_error": True}

    def _effort_note(self, has_tools: bool) -> str:
        """The limit a request meets when it sends tools with a reasoning effort to OpenAI's chat endpoint.

        Empty when there is no conflict. The first conflict of a provider instance is logged as a warning.
        """
        effort = self.config.reasoning_effort
        if not (has_tools and effort and effort.lower() != "none" and is_official_openai(self.config)):
            return ""
        note = _EFFORT_WITH_TOOLS_NOTE.format(effort=effort)
        if not self._effort_warned:
            self._effort_warned = True
            logger.warning(note)
        return note

    def _effort_kwargs(self) -> dict[str, Any]:
        """The ``reasoning_effort`` request field, present only when the setting is."""
        return {"reasoning_effort": self.config.reasoning_effort} if self.config.reasoning_effort else {}

    def _build_tools(self, tools: Sequence[ToolSpec]) -> list[dict[str, Any]]:
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

            if role == "user" and isinstance(content, list) and any(b.get("type") == "image" for b in content if isinstance(b, dict)):
                api_messages.append({"role": "user", "content": _content_parts(content)})
                continue

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
