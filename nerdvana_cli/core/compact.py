# nerdvana_cli/core/compact.py
"""AI-powered context compression.

Uses the body of compress-context.skill as the prompt to summarize conversations.
Falls back to FALLBACK_PROMPT when the skill file is not found.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

from nerdvana_cli.types import Message, Role

logger = logging.getLogger(__name__)

MAX_CONSECUTIVE_FAILURES = 3

FALLBACK_PROMPT = """\
CRITICAL: Respond with TEXT ONLY. Do NOT call any tools.

Summarize the conversation below into a concise handoff document.
Cover: current objective, progress, key decisions, errors and fixes, \
pending tasks, and the last assistant response verbatim.

REMINDER: Do NOT call any tools.
"""


@dataclass
class CompactionState:
    consecutive_failures: int = 0
    total_compactions: int = 0
    max_failures: int = MAX_CONSECUTIVE_FAILURES

    @property
    def is_circuit_open(self) -> bool:
        return self.consecutive_failures >= self.max_failures

    def record_failure(self) -> None:
        self.consecutive_failures += 1
        logger.warning("ai_compact failure #%d", self.consecutive_failures)

    def record_success(self) -> None:
        self.consecutive_failures = 0
        self.total_compactions += 1


def _messages_to_text(messages: list[Message]) -> str:
    lines: list[str] = []
    for msg in messages:
        role = str(msg.role).upper()
        content = msg.content if isinstance(msg.content, str) else str(msg.content)
        if msg.tool_uses:
            names = [t.get("name", "?") for t in msg.tool_uses]
            suffix = f" [tools: {', '.join(names)}]"
            content = (content + suffix) if content else suffix
        lines.append(f"{role}: {content}")
    return "\n\n".join(lines)


def _extract_summary(raw: str) -> str:
    cleaned = re.sub(r"<analysis>.*?</analysis>", "", raw, flags=re.DOTALL).strip()
    m = re.search(r"<summary>(.*?)</summary>", cleaned, flags=re.DOTALL)
    return m.group(1).strip() if m else cleaned


async def ai_compact(
    messages: list[Message],
    provider: Any,
    state: CompactionState,
    *,
    prompt: str,
) -> Message | None:
    """Perform AI summarization using compress-context.skill body as prompt.

    Returns:
        Summary Message — None on failure or when circuit is open.
    """
    if state.is_circuit_open:
        logger.info("ai_compact skipped — circuit open (%d failures)", state.consecutive_failures)
        return None

    history_text = _messages_to_text(messages)

    # Truncate to avoid exceeding provider context window.
    # Rough heuristic: 1 token ≈ 4 chars; keep at most 50% of a 128k window.
    max_chars = 128_000
    if len(history_text) > max_chars:
        history_text = history_text[-max_chars:]

    full_content = f"{prompt}\n\n---\n\nCONVERSATION HISTORY:\n\n{history_text}"

    try:
        result = await provider.send(
            system_prompt="You are a conversation summarizer. Respond with plain text only.",
            messages=[{"role": "user", "content": full_content}],
            tools=[],
        )
        raw = result.get("content", "")
        if not raw or not raw.strip():
            state.record_failure()
            logger.warning("ai_compact: empty response")
            return None

        summary = _extract_summary(raw)
        state.record_success()
        logger.info("ai_compact success (compaction #%d)", state.total_compactions)
        return Message(role=Role.USER, content=f"[CONTEXT SUMMARY]\n\n{summary}")

    except Exception as exc:
        state.record_failure()
        logger.warning("ai_compact exception: %s", exc)
        return None


def split_into_blocks(
    messages: list[dict[str, str]],
    max_block_size: int = 10,
) -> list[list[dict[str, str]]]:
    """Split conversation into blocks based on message count.

    Args:
        messages: List of message dicts with 'role' and 'content'
        max_block_size: Maximum messages per block

    Returns:
        List of message blocks
    """
    if not messages:
        return []

    blocks: list[list[dict[str, str]]] = []
    current_block: list[dict[str, str]] = []

    for msg in messages:
        current_block.append(msg)
        if len(current_block) >= max_block_size:
            blocks.append(current_block)
            current_block = []

    if current_block:
        blocks.append(current_block)

    return blocks


def summarize_block(block: list[dict[str, str]]) -> str:
    """Create extractive summary from a conversation block.

    Args:
        block: List of messages to summarize

    Returns:
        Concise summary string
    """
    if not block:
        return ""

    user_msgs = [m["content"] for m in block if m.get("role") == "user"]
    assistant_msgs = [m["content"] for m in block if m.get("role") == "assistant"]

    return _extractive_summary_simple(user_msgs, assistant_msgs)


def _extractive_summary_simple(
    user_msgs: list[str],
    assistant_msgs: list[str],
) -> str:
    """Create extractive summary from message lists."""
    parts = []
    if user_msgs:
        parts.append(f"User: {user_msgs[0][:100]}")
    if assistant_msgs:
        parts.append(f"Assistant: {assistant_msgs[0][:100]}")
    return " | ".join(parts)


def compact_with_blocks(
    messages: list[dict[str, str]],
    keep_recent: int = 2,
    max_block_size: int = 10,
) -> list[dict[str, str]]:
    """Compact conversation using block splitting and summarization.

    Args:
        messages: Full conversation messages
        keep_recent: Number of recent blocks to keep intact
        max_block_size: Maximum messages per block

    Returns:
        Compacted message list with summaries for old blocks
    """
    if not messages:
        return []

    blocks = split_into_blocks(messages, max_block_size)

    if len(blocks) <= keep_recent:
        return messages

    # Keep recent blocks intact
    recent_blocks = blocks[-keep_recent:]
    old_blocks = blocks[:-keep_recent]

    # Summarize old blocks
    compacted: list[dict[str, str]] = []
    for block in old_blocks:
        summary = summarize_block(block)
        if summary:
            compacted.append({
                "role": "system",
                "content": f"[Context summary]: {summary}",
            })

    # Add recent blocks
    for block in recent_blocks:
        compacted.extend(block)

    return compacted
