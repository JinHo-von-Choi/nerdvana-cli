from __future__ import annotations
import pytest
from unittest.mock import AsyncMock, MagicMock
from nerdvana_cli.core.compact import (
    CompactionState, ai_compact, _messages_to_text, _extract_summary,
    MAX_CONSECUTIVE_FAILURES, FALLBACK_PROMPT,
)
from nerdvana_cli.types import Message, Role


def make_msgs(n):
    return [Message(role=Role.USER, content=f"msg {i}") for i in range(n)]


def test_compaction_state_initial():
    s = CompactionState()
    assert s.consecutive_failures == 0
    assert not s.is_circuit_open

def test_circuit_opens_after_max_failures():
    s = CompactionState()
    for _ in range(MAX_CONSECUTIVE_FAILURES):
        s.record_failure()
    assert s.is_circuit_open

def test_circuit_resets_on_success():
    s = CompactionState()
    s.record_failure(); s.record_failure()
    s.record_success()
    assert s.consecutive_failures == 0
    assert not s.is_circuit_open

def test_messages_to_text():
    msgs = [Message(role=Role.USER, content="hello"), Message(role=Role.ASSISTANT, content="world")]
    text = _messages_to_text(msgs)
    assert "USER: hello" in text
    assert "ASSISTANT: world" in text

def test_extract_summary_strips_analysis():
    raw = "<analysis>scratchpad</analysis>\n<summary>clean content</summary>"
    assert _extract_summary(raw) == "clean content"

def test_extract_summary_no_tags():
    assert _extract_summary("plain text") == "plain text"

def test_fallback_prompt_is_string():
    assert isinstance(FALLBACK_PROMPT, str) and len(FALLBACK_PROMPT) > 50

@pytest.mark.asyncio
async def test_ai_compact_success():
    provider = MagicMock()
    provider.send = AsyncMock(return_value={"content": "<summary>OK summary</summary>"})
    state = CompactionState()
    result = await ai_compact(make_msgs(10), provider, state, prompt="Summarize:")
    assert result is not None
    assert "OK summary" in result.content
    assert state.consecutive_failures == 0

@pytest.mark.asyncio
async def test_ai_compact_empty_response_records_failure():
    provider = MagicMock()
    provider.send = AsyncMock(return_value={"content": ""})
    state = CompactionState()
    result = await ai_compact(make_msgs(5), provider, state, prompt="Summarize:")
    assert result is None
    assert state.consecutive_failures == 1

@pytest.mark.asyncio
async def test_ai_compact_exception_records_failure():
    provider = MagicMock()
    provider.send = AsyncMock(side_effect=RuntimeError("API down"))
    state = CompactionState()
    result = await ai_compact(make_msgs(5), provider, state, prompt="Summarize:")
    assert result is None
    assert state.consecutive_failures == 1

@pytest.mark.asyncio
async def test_ai_compact_skips_when_circuit_open():
    provider = MagicMock()
    provider.send = AsyncMock()
    state = CompactionState()
    for _ in range(MAX_CONSECUTIVE_FAILURES):
        state.record_failure()
    result = await ai_compact(make_msgs(5), provider, state, prompt="Summarize:")
    assert result is None
    provider.send.assert_not_called()

def test_compaction_state_custom_max_failures():
    s = CompactionState(max_failures=5)
    for _ in range(4):
        s.record_failure()
    assert not s.is_circuit_open
    s.record_failure()
    assert s.is_circuit_open
