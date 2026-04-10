"""Tests for session recovery hooks."""
from types import SimpleNamespace
from unittest.mock import MagicMock

from nerdvana_cli.core.builtin_hooks import (
    context_limit_recovery,
    json_parse_recovery,
)
from nerdvana_cli.core.hooks import HookContext, HookEvent


def test_context_limit_recovery_injects_message() -> None:
    prev_msg = SimpleNamespace(role="user", content="previous user message")
    ctx = HookContext(
        event       = HookEvent.AFTER_API_CALL,
        tools       = [],
        settings    = MagicMock(),
        messages    = [prev_msg],
        stop_reason = "max_tokens",
    )
    result = context_limit_recovery(ctx)
    assert len(result.inject_messages) > 0
    content = result.inject_messages[0]["content"]
    assert "continue" in content.lower() or "계속" in content


def test_context_limit_recovery_noop_on_end_turn() -> None:
    ctx = HookContext(
        event       = HookEvent.AFTER_API_CALL,
        tools       = [],
        settings    = MagicMock(),
        messages    = [],
        stop_reason = "end_turn",
    )
    result = context_limit_recovery(ctx)
    assert result.inject_messages == []


def test_json_parse_recovery_injects_message() -> None:
    ctx = HookContext(
        event       = HookEvent.AFTER_TOOL,
        tools       = [],
        settings    = MagicMock(),
        tool_name   = "SomeTool",
        tool_result = '{"invalid json',
        extra       = {"json_error": "Expecting value: line 1 column 2 (char 1)"},
    )
    result = json_parse_recovery(ctx)
    assert len(result.inject_messages) > 0
    assert "JSON" in result.inject_messages[0]["content"]


def test_json_parse_recovery_noop_when_no_error() -> None:
    ctx = HookContext(
        event    = HookEvent.AFTER_TOOL,
        tools    = [],
        settings = MagicMock(),
        extra    = {},
    )
    result = json_parse_recovery(ctx)
    assert result.inject_messages == []
