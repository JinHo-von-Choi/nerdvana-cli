"""Hook system for lifecycle event handling."""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

logger = logging.getLogger(__name__)


class HookEvent(StrEnum):
    SESSION_START = "session_start"
    SESSION_END = "session_end"
    BEFORE_TOOL = "before_tool"
    AFTER_TOOL = "after_tool"
    BEFORE_API_CALL = "before_api_call"
    AFTER_API_CALL = "after_api_call"


@dataclass
class HookContext:
    """Context passed to hook handlers."""
    event: HookEvent
    settings: Any = None
    tools: list[Any] = field(default_factory=list)
    messages: list[Any] = field(default_factory=list)
    tool_name: str = ""
    tool_input: dict[str, Any] = field(default_factory=dict)
    tool_result: Any = None
    stop_reason: str | None = None  # "max_tokens", "end_turn", "tool_use"
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class HookResult:
    """Result from a hook handler.

    Fields:
        allow: If False, block the associated action.
        message: Message to display to the user.
        inject_messages: List of dicts to insert into the conversation stream.
            Must follow standard message format (role="user"/"assistant"/etc.).
            For sticky information that belongs in the system_prompt, use
            system_prompt_append instead of inject_messages.
        system_prompt_append: Channel exclusive to SESSION_START hooks.
            The returned string is cumulatively appended to the system_prompt
            on every turn, persisting for the model across the entire session.
    """
    allow: bool = True
    message: str = ""
    inject_messages: list[dict[str, Any]] = field(default_factory=list)
    system_prompt_append: str = ""


HookHandler = Callable[[HookContext], HookResult | None]


class HookEngine:
    """Central hook registry and executor."""

    def __init__(self) -> None:
        self._handlers: dict[HookEvent, list[HookHandler]] = defaultdict(list)

    def register(self, event: HookEvent, handler: HookHandler) -> None:
        self._handlers[event].append(handler)

    def unregister(self, event: HookEvent, handler: HookHandler) -> None:
        self._handlers[event] = [h for h in self._handlers[event] if h is not handler]

    def fire(self, context: HookContext) -> list[HookResult]:
        results = []
        for handler in self._handlers.get(context.event, []):
            try:
                result = handler(context)
                if result is not None:
                    results.append(result)
            except Exception as e:
                logger.warning("Hook handler %s failed: %s", handler.__name__, e)
        return results

    def has_handlers(self, event: HookEvent) -> bool:
        return bool(self._handlers.get(event))
