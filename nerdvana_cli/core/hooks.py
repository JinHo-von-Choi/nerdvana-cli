"""Hook system for lifecycle event handling."""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Callable

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
        allow: False이면 해당 작업을 차단한다.
        message: 사용자에게 표시할 메시지.
        inject_messages: 대화 스트림(메시지 배열)에 삽입할 dict 목록.
            role="user" / "assistant" 등 표준 메시지 형식을 따른다.
            system_prompt에 들어갈 sticky 정보는 inject_messages 대신
            system_prompt_append를 사용한다.
        modified_input: BEFORE_TOOL hook에서 도구 입력을 수정할 때 사용한다.
        system_prompt_append: SESSION_START hook 전용 통로.
            반환된 문자열은 매 턴 system_prompt 끝에 누적 append되어
            세션 전체에 걸쳐 모델에게 유지된다.
    """
    allow: bool = True
    message: str = ""
    inject_messages: list[dict[str, Any]] = field(default_factory=list)
    modified_input: dict[str, Any] | None = None
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
