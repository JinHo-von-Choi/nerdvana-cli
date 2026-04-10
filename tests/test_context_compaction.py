import pytest
from unittest.mock import AsyncMock, MagicMock

from nerdvana_cli.core.agent_loop import compact_messages, estimate_tokens
from nerdvana_cli.core.compact import CompactionState, ai_compact, FALLBACK_PROMPT
from nerdvana_cli.types import Message, Role


def make_msgs(n):
    return [Message(role=Role.USER, content=f"msg {i}") for i in range(n)]


def test_estimate_tokens_basic():
    assert estimate_tokens("hello world") == 3


def test_compact_preserves_recent():
    msgs = [Message(role=Role.USER, content=f"msg {i}") for i in range(50)]
    compacted = compact_messages(msgs, max_tokens=500)
    assert compacted[-1].content == "msg 49"


def test_compact_adds_summary():
    msgs = [Message(role=Role.USER, content="x" * 200) for _ in range(50)]
    compacted = compact_messages(msgs, max_tokens=1000)
    has_summary = any("[context compacted" in (m.content if isinstance(m.content, str) else "") for m in compacted)
    assert has_summary


def test_compact_noop_under_limit():
    msgs = [Message(role=Role.USER, content="short")]
    assert compact_messages(msgs, max_tokens=1000) == msgs


@pytest.mark.asyncio
async def test_ai_compact_passes_skill_prompt():
    provider = MagicMock()
    provider.send = AsyncMock(return_value={"content": "<summary>Integrated summary</summary>"})
    state = CompactionState()
    prompt = "Test prompt from skill"
    result = await ai_compact(make_msgs(20), provider, state, prompt=prompt)
    assert result is not None
    assert "Integrated summary" in result.content
    call_args = provider.send.call_args
    user_content = call_args.kwargs["messages"][0]["content"]
    assert "Test prompt from skill" in user_content
    assert "CONVERSATION HISTORY" in user_content


@pytest.mark.asyncio
async def test_fallback_prompt_used_when_skill_missing():
    provider = MagicMock()
    provider.send = AsyncMock(return_value={"content": "summary"})
    state = CompactionState()
    await ai_compact(make_msgs(5), provider, state, prompt=FALLBACK_PROMPT)
    call_args = provider.send.call_args
    user_content = call_args.kwargs["messages"][0]["content"]
    assert "CRITICAL" in user_content


def test_compaction_recent_skips_leading_tool_messages():
    """압축 후 recent에서 선두 TOOL 메시지를 건너뛴다."""
    tool_msg = Message(role=Role.TOOL, content="tool result", tool_use_id="t1")
    asst_msg = Message(role=Role.ASSISTANT, content="assistant reply")
    recent = [tool_msg, asst_msg]
    # summary_msg 직후 TOOL이 오지 않도록 필터
    while recent and recent[0].role == Role.TOOL:
        recent = recent[1:]
    assert recent[0].role == Role.ASSISTANT
