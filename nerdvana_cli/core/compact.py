# nerdvana_cli/core/compact.py
"""AI-powered context compression.

compress-context.skill의 body를 프롬프트로 사용하여 대화를 요약한다.
스킬 파일이 없으면 FALLBACK_PROMPT를 사용한다.
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
    """compress-context.skill body를 프롬프트로 AI 요약 수행.

    Returns:
        요약 Message — 실패 또는 circuit open이면 None.
    """
    if state.is_circuit_open:
        logger.info("ai_compact skipped — circuit open (%d failures)", state.consecutive_failures)
        return None

    history_text = _messages_to_text(messages)
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
