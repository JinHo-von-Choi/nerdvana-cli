"""Tests for ralph_loop_check hook and ultrawork detection."""
from types import SimpleNamespace
from unittest.mock import MagicMock

from nerdvana_cli.core.agent_loop import _is_ultrawork
from nerdvana_cli.core.builtin_hooks import ralph_loop_check
from nerdvana_cli.core.hooks import HookContext, HookEvent


def test_ultrawork_korean_sentence() -> None:
    assert _is_ultrawork("ultrawork 로 분석해줘") is True


def test_ultrawork_alias() -> None:
    assert _is_ultrawork("ulw this codebase") is True


def test_ultrawork_not_matched() -> None:
    assert _is_ultrawork("analyze the code") is False


def test_ultrawork_case_insensitive() -> None:
    assert _is_ultrawork("Run ULW NOW") is True


def test_ralph_loop_detects_todo() -> None:
    msg = SimpleNamespace(
        role    = "assistant",
        content = "Here is the code:\n\n# TODO: implement this\ndef foo(): pass",
    )
    ctx = HookContext(
        event       = HookEvent.AFTER_API_CALL,
        tools       = [],
        settings    = MagicMock(),
        messages    = [msg],
        stop_reason = "end_turn",
    )
    result = ralph_loop_check(ctx)
    assert len(result.inject_messages) > 0
    assert "TODO" in result.inject_messages[0]["content"]


def test_ralph_loop_detects_not_implemented() -> None:
    msg = SimpleNamespace(
        role    = "assistant",
        content = "def bar():\n    raise NotImplementedError()",
    )
    ctx = HookContext(
        event       = HookEvent.AFTER_API_CALL,
        tools       = [],
        settings    = MagicMock(),
        messages    = [msg],
        stop_reason = "end_turn",
    )
    result = ralph_loop_check(ctx)
    assert len(result.inject_messages) > 0


def test_ralph_loop_clean_code_noop() -> None:
    msg = SimpleNamespace(
        role    = "assistant",
        content = "def bar():\n    return 42",
    )
    ctx = HookContext(
        event       = HookEvent.AFTER_API_CALL,
        tools       = [],
        settings    = MagicMock(),
        messages    = [msg],
        stop_reason = "end_turn",
    )
    result = ralph_loop_check(ctx)
    assert result.inject_messages == []


def test_ralph_loop_noop_on_tool_use() -> None:
    msg = SimpleNamespace(
        role    = "assistant",
        content = "# TODO: later",
    )
    ctx = HookContext(
        event       = HookEvent.AFTER_API_CALL,
        tools       = [],
        settings    = MagicMock(),
        messages    = [msg],
        stop_reason = "tool_use",
    )
    result = ralph_loop_check(ctx)
    assert result.inject_messages == []
