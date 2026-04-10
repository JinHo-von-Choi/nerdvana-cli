"""Verify Message dataclass has tool_uses field."""
from nerdvana_cli.types import Message, Role


def test_message_has_tool_uses_field():
    msg = Message(role=Role.ASSISTANT, content="test")
    assert hasattr(msg, "tool_uses")
    assert msg.tool_uses == []


def test_message_tool_uses_assigned():
    tool_data = [{"id": "call_0", "name": "Bash", "input": {"command": "ls"}}]
    msg = Message(role=Role.ASSISTANT, content="test", tool_uses=tool_data)
    assert msg.tool_uses == tool_data


def test_message_tool_uses_not_shared():
    msg1 = Message(role=Role.ASSISTANT, content="a")
    msg2 = Message(role=Role.ASSISTANT, content="b")
    msg1.tool_uses.append({"id": "1", "name": "X", "input": {}})
    assert msg2.tool_uses == []
