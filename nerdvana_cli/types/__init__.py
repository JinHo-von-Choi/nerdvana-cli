"""Core type definitions for NerdVana CLI."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class Role(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"
    TOOL = "tool"


class StopReason(StrEnum):
    END_TURN = "end_turn"
    MAX_TOKENS = "max_tokens"
    STOP_SEQUENCE = "stop_sequence"
    TOOL_USE = "tool_use"


class PermissionBehavior(StrEnum):
    ALLOW = "allow"
    DENY = "deny"
    ASK = "ask"


@dataclass
class ToolUseBlock:
    id: str
    name: str
    input: dict[str, Any]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ToolUseBlock:
        return cls(
            id=data.get("id", str(uuid.uuid4())[:8]),
            name=data["name"],
            input=data.get("input", {}),
        )


@dataclass
class ToolResult:
    tool_use_id: str
    content: str
    is_error: bool = False
    tokens:   int  = 0  # input+output tokens consumed by this tool call (0 = unknown)


@dataclass
class Message:
    role: Role
    content: str | list[dict[str, Any]]
    tool_use_id: str | None = None
    is_error: bool = False
    tool_uses: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class PermissionResult:
    behavior: PermissionBehavior
    message: str = ""
    updated_input: dict[str, Any] | None = None


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_tokens: int = 0
    cache_read_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass
class ToolProgress:
    tool_name: str
    status: str
    detail: str = ""


@dataclass
class SessionState:
    """Mutable state for the agent loop — replaced atomically each turn."""

    messages: list[Message] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    turn_count: int = 1
    file_state: dict[str, str] = field(default_factory=dict)
    tool_results: list[ToolResult] = field(default_factory=list)
