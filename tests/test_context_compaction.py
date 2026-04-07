from nerdvana_cli.core.agent_loop import compact_messages, estimate_tokens
from nerdvana_cli.types import Message, Role


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
