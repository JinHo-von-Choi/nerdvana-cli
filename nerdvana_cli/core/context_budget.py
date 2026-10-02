"""Context window accounting for the agent loop.

Author: 최진호
Date:   2026-10-03

The provider's own ``input_tokens`` for the last request is the most accurate
figure available: it already counts the system prompt, tool schemas and every
message sent. Messages appended after that request are added on top with a
fast estimate. Before the first usage report, or after compaction rewrote the
history, the whole request is estimated, system prompt and tool schemas
included.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from nerdvana_cli.core.token_estimator import approx_tokens


def message_tokens(messages: Sequence[Any]) -> int:
    """Estimated tokens of *messages*, tool calls included."""
    total = 0
    for message in messages:
        content = message.content
        total  += approx_tokens(content if isinstance(content, str) else json.dumps(content, ensure_ascii=False))
        if message.tool_uses:
            total += approx_tokens(json.dumps(message.tool_uses, ensure_ascii=False))
    return total


def request_overhead(system_prompt: str, tools: Sequence[Any]) -> int:
    """Estimated tokens of the system prompt and the tool declarations."""
    declarations = [
        {
            "name":         getattr(tool, "name", ""),
            "description":  getattr(tool, "description_text", ""),
            "input_schema": getattr(tool, "input_schema", {}),
        }
        for tool in tools
    ]
    return approx_tokens(system_prompt) + approx_tokens(json.dumps(declarations, ensure_ascii=False))


class ContextBudget:
    """Tracks how much of the context window the next request will use."""

    def __init__(self) -> None:
        self._overhead      = 0
        self._anchor:       int | None = None
        self._anchor_index  = 0

    def set_overhead(self, system_prompt: str, tools: Sequence[Any]) -> None:
        """Record the fixed part of every request for the estimate path."""
        self._overhead = request_overhead(system_prompt, tools)

    def record_usage(self, input_tokens: int, messages_sent: int) -> None:
        """Anchor on the provider's count for a request of *messages_sent* messages."""
        if input_tokens > 0:
            self._anchor       = input_tokens
            self._anchor_index = messages_sent

    def reset(self) -> None:
        """Forget the anchor; the history no longer matches what was measured."""
        self._anchor = None

    def current(self, messages: Sequence[Any]) -> int:
        """Tokens the next request built from *messages* is expected to use."""
        if self._anchor is not None and self._anchor_index <= len(messages):
            return self._anchor + message_tokens(messages[self._anchor_index:])
        return self._overhead + message_tokens(messages)
