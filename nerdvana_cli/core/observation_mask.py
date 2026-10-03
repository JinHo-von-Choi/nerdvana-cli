"""Clearing old tool output from the history before it is compacted.

Author: 최진호
Date:   2026-10-03

Old results of read-type tools (file reads, searches, shell output, web and MCP output) are the bulk
of a long history and the part the model needs least: it can call the tool again. This module
replaces their text with a short placeholder. The assistant message that made the call and the
tool message that answers it stay in place, so the tool_use/tool_result pairing is intact.

Clearing happens in batches, not as a window that slides every turn, because every change to an
old message changes the request prefix and makes the provider reread it. Nothing changes until the
clearable results add up to ``trigger_tokens``; then all of them are cleared at once, and the
history stays byte-stable until the next batch.

Never cleared: results of write, edit and todo tools, error results, results of the last
``keep_last`` tool calls, and any message holding ``<skill_content``. A file read whose lines carry
``N#hhhhhh`` anchors is cleared like any other read: the model reads the file again when it needs to
edit it, and that read is among the newest results, which stay.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from nerdvana_cli.core.token_estimator import approx_tokens
from nerdvana_cli.types import Role

# Tools whose output can be fetched again. Anything else (edits, writes, todos, diagnostics) is kept.
MASKABLE_TOOLS = frozenset({
    "FileRead", "Glob", "Grep", "Bash", "Parism", "WebFetch", "WebSearch",
    "symbol_overview", "find_symbol", "find_referencing_symbols",
    "lsp_goto_definition", "lsp_find_references",
})
MCP_TOOL_PREFIX = "mcp__"

_PLACEHOLDER  = re.compile(r"^\[output of .+ cleared \(\d+ chars\); call it again if needed\]$")


@dataclass(frozen=True)
class MaskResult:
    """What one pass changed: results replaced and the estimated tokens that saved."""

    masked:       int
    tokens_saved: int


def placeholder_for(tool: str, chars: int) -> str:
    """The text that stands in for *chars* characters of output from *tool*."""
    return f"[output of {tool} cleared ({chars} chars); call it again if needed]"


def _is_maskable_tool(name: str) -> bool:
    return name in MASKABLE_TOOLS or name.startswith(MCP_TOOL_PREFIX)


def _calls_by_id(messages: Sequence[Any]) -> dict[str, tuple[str, dict[str, Any]]]:
    """Tool call id to (tool name, input) for every call in *messages*."""
    calls: dict[str, tuple[str, dict[str, Any]]] = {}
    for message in messages:
        if message.role == Role.ASSISTANT:
            for use in message.tool_uses:
                calls[str(use.get("id", ""))] = (str(use.get("name", "")), use.get("input") or {})
    return calls


def _clearable(message: Any, name: str) -> bool:
    """Whether the result in *message*, a tool message answering a call to *name*, may be replaced."""
    content = message.content
    return (
        isinstance(content, str)
        and not message.is_error
        and _is_maskable_tool(name)
        and "<skill_content" not in content
        and not _PLACEHOLDER.match(content)
    )


def _tool_name(calls: dict[str, tuple[str, dict[str, Any]]], message: Any) -> str:
    return calls.get(str(message.tool_use_id or ""), ("", {}))[0]


def _clearable_indexes(messages: Sequence[Any], calls: dict[str, tuple[str, dict[str, Any]]], keep_last: int) -> list[int]:
    """Indexes of the old tool results that may be cleared."""
    tool_at   = [i for i, m in enumerate(messages) if m.role == Role.TOOL]
    protected = set(tool_at[-keep_last:]) if keep_last > 0 else set()
    return [i for i in tool_at if i not in protected and _clearable(messages[i], _tool_name(calls, messages[i]))]


def mask_observations(messages: Sequence[Any], *, keep_last: int, trigger_tokens: int) -> MaskResult:
    """Clear the old read-type tool results in *messages*, in place, once they add up to *trigger_tokens*.

    Returns what was replaced; nothing changes (and the result is empty) while the clearable
    results are still below the trigger.
    """
    calls   = _calls_by_id(messages)
    indexes = _clearable_indexes(messages, calls, keep_last)
    if sum(approx_tokens(messages[i].content) for i in indexes) <= trigger_tokens:
        return MaskResult(0, 0)
    saved = 0
    for index in indexes:
        message = messages[index]
        text    = placeholder_for(_tool_name(calls, message), len(message.content))
        saved  += approx_tokens(message.content) - approx_tokens(text)
        message.content = text
    return MaskResult(len(indexes), saved)
