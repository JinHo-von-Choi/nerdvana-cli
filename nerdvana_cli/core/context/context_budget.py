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

from nerdvana_cli.core.context.token_estimator import approx_tokens
from nerdvana_cli.core.state.compaction_block import last_compaction_index

# What an image costs in a request, whatever its size on disk: providers bill by pixels, a few thousand tokens at most.
IMAGE_TOKENS = 1500


def _blocks_tokens(blocks: Sequence[Any]) -> int:
    """Estimated tokens of content blocks: images at a flat figure instead of their base64 text."""
    plain  = [b for b in blocks if not (isinstance(b, dict) and b.get("type") == "image")]
    images = len(blocks) - len(plain)
    return approx_tokens(json.dumps(plain, ensure_ascii=False)) + images * IMAGE_TOKENS


def message_tokens(messages: Sequence[Any]) -> int:
    """Estimated tokens of *messages*, tool calls included."""
    total = 0
    for message in messages:
        content = message.content
        total  += approx_tokens(content) if isinstance(content, str) else _blocks_tokens(content)
        if message.tool_uses:
            total += approx_tokens(json.dumps(message.tool_uses, ensure_ascii=False))
        if getattr(message, "provider_blocks", None):
            total += approx_tokens(json.dumps(message.provider_blocks, ensure_ascii=False))
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
        """Tokens the next request built from *messages* is expected to use.

        A message that carries a compaction block stands for everything before it (the provider leaves
        those out), so without a measurement only the messages from the last such block on are estimated.
        """
        start  = last_compaction_index(messages)
        anchor = self._anchor
        if anchor is not None and self._anchor_index <= len(messages) and (start == 0 or self._anchor_index > start):
            return anchor + message_tokens(messages[self._anchor_index:])
        return self._overhead + message_tokens(messages[start:])
