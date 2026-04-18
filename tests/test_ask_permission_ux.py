"""ASK permission UX regression tests — M-4.

Verifies:
- Non-TTY (pipe / CI): ASK → automatic DENY, no blocking
- Interactive TTY: 'y'/'yes' → ALLOW; '' / 'n' / 'no' / 'x' → DENY
- EOFError on stdin → DENY (fail-safe)
- ToolExecutor integration: ASK path wires through _ask_user_permission

작성자: 최진호
작성일: 2026-04-18
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest

from nerdvana_cli.core.tool import ToolContext
from nerdvana_cli.core.tool_executor import ToolExecutor


# ---------------------------------------------------------------------------
# _ask_user_permission unit tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_non_tty_always_denies() -> None:
    executor = ToolExecutor(
        registry  = MagicMock(),
        hooks     = MagicMock(),
        settings  = None,
    )
    with patch.object(sys.stdin, "isatty", return_value=False):
        result = await executor._ask_user_permission(
            tool_name = "RegisterExternalProject",
            message   = "Will write to filesystem",
        )
    assert result is False


@pytest.mark.asyncio
async def test_tty_y_grants() -> None:
    executor = ToolExecutor(
        registry  = MagicMock(),
        hooks     = MagicMock(),
        settings  = None,
    )
    with (
        patch.object(sys.stdin, "isatty", return_value=True),
        patch("builtins.input", return_value="y"),
    ):
        result = await executor._ask_user_permission("TestTool", "test msg")
    assert result is True


@pytest.mark.asyncio
async def test_tty_yes_grants() -> None:
    executor = ToolExecutor(
        registry  = MagicMock(),
        hooks     = MagicMock(),
        settings  = None,
    )
    with (
        patch.object(sys.stdin, "isatty", return_value=True),
        patch("builtins.input", return_value="yes"),
    ):
        result = await executor._ask_user_permission("TestTool", "test msg")
    assert result is True


@pytest.mark.asyncio
async def test_tty_yes_case_insensitive() -> None:
    executor = ToolExecutor(
        registry  = MagicMock(),
        hooks     = MagicMock(),
        settings  = None,
    )
    for reply in ("Y", "YES", "Yes"):
        with (
            patch.object(sys.stdin, "isatty", return_value=True),
            patch("builtins.input", return_value=reply),
        ):
            result = await executor._ask_user_permission("TestTool", "msg")
        assert result is True, f"Reply {reply!r} should have been accepted"


@pytest.mark.asyncio
@pytest.mark.parametrize("reply", ["", "n", "N", "no", "NO", "x", "cancel", "  "])
async def test_tty_non_yes_denies(reply: str) -> None:
    executor = ToolExecutor(
        registry  = MagicMock(),
        hooks     = MagicMock(),
        settings  = None,
    )
    with (
        patch.object(sys.stdin, "isatty", return_value=True),
        patch("builtins.input", return_value=reply),
    ):
        result = await executor._ask_user_permission("TestTool", "msg")
    assert result is False, f"Reply {reply!r} should have been denied"


@pytest.mark.asyncio
async def test_tty_eof_denies() -> None:
    executor = ToolExecutor(
        registry  = MagicMock(),
        hooks     = MagicMock(),
        settings  = None,
    )
    with (
        patch.object(sys.stdin, "isatty", return_value=True),
        patch("builtins.input", side_effect=EOFError),
    ):
        result = await executor._ask_user_permission("TestTool", "msg")
    assert result is False


@pytest.mark.asyncio
async def test_tty_oserror_denies() -> None:
    executor = ToolExecutor(
        registry  = MagicMock(),
        hooks     = MagicMock(),
        settings  = None,
    )
    with (
        patch.object(sys.stdin, "isatty", return_value=True),
        patch("builtins.input", side_effect=OSError("stdin broken")),
    ):
        result = await executor._ask_user_permission("TestTool", "msg")
    assert result is False


# ---------------------------------------------------------------------------
# ToolExecutor.run_batch integration — ASK flows through the new gate
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_run_batch_ask_non_tty_returns_error() -> None:
    """run_batch with an ASK tool in non-TTY must return is_error=True."""
    from nerdvana_cli.core.loop_state import LoopState
    from nerdvana_cli.core.tool import BaseTool, ToolRegistry
    from nerdvana_cli.types import PermissionBehavior, PermissionResult, ToolResult

    class _AskTool(BaseTool):
        name             = "_TestAskTool"
        description_text = "test"
        input_schema     = {"type": "object", "properties": {}}

        def check_permissions(self, args, context):
            return PermissionResult(behavior=PermissionBehavior.ASK, message="test ask")

        async def call(self, args, context, can_use_tool, on_progress=None):
            return ToolResult(tool_use_id="", content="should not reach here")

    reg = ToolRegistry()
    reg.register(_AskTool())

    hooks    = MagicMock()
    hooks.fire.return_value = []
    executor = ToolExecutor(registry=reg, hooks=hooks, settings=None)

    fake_call = {"id": "tc-001", "name": "_TestAskTool", "input": {}}
    context   = ToolContext()
    state     = LoopState(iteration=1, stop_reason="continue", continuation_hint=None, token_budget_used=0, session_id="test")

    with patch.object(sys.stdin, "isatty", return_value=False):
        results = await executor.run_batch([fake_call], context=context)

    assert results[0].is_error is True
    assert "denied" in results[0].content.lower()


@pytest.mark.asyncio
async def test_run_batch_ask_tty_yes_proceeds() -> None:
    """run_batch with an ASK tool in TTY where user types 'y' must execute."""
    from nerdvana_cli.core.loop_state import LoopState
    from nerdvana_cli.core.tool import BaseTool, ToolRegistry
    from nerdvana_cli.types import PermissionBehavior, PermissionResult, ToolResult

    class _AskTool(BaseTool):
        name             = "_TestAskToolY"
        description_text = "test"
        input_schema     = {"type": "object", "properties": {}}

        def check_permissions(self, args, context):
            return PermissionResult(behavior=PermissionBehavior.ASK, message="test ask")

        async def call(self, args, context, can_use_tool, on_progress=None):
            return ToolResult(tool_use_id="", content="executed successfully")

    reg = ToolRegistry()
    reg.register(_AskTool())

    hooks    = MagicMock()
    hooks.fire.return_value = []
    executor = ToolExecutor(registry=reg, hooks=hooks, settings=None)

    fake_call = {"id": "tc-002", "name": "_TestAskToolY", "input": {}}
    context   = ToolContext()
    state     = LoopState(iteration=1, stop_reason="continue", continuation_hint=None, token_budget_used=0, session_id="test")

    with (
        patch.object(sys.stdin, "isatty", return_value=True),
        patch("builtins.input", return_value="y"),
    ):
        results = await executor.run_batch([fake_call], context=context)

    assert results[0].is_error is False
    assert "executed successfully" in results[0].content


@pytest.mark.asyncio
async def test_run_batch_deny_unchanged() -> None:
    """DENY permission must still produce is_error=True and never call _ask."""
    from nerdvana_cli.core.loop_state import LoopState
    from nerdvana_cli.core.tool import BaseTool, ToolRegistry
    from nerdvana_cli.types import PermissionBehavior, PermissionResult, ToolResult

    class _DenyTool(BaseTool):
        name             = "_TestDenyTool"
        description_text = "test"
        input_schema     = {"type": "object", "properties": {}}

        def check_permissions(self, args, context):
            return PermissionResult(behavior=PermissionBehavior.DENY, message="blocked")

        async def call(self, args, context, can_use_tool, on_progress=None):
            return ToolResult(tool_use_id="", content="reached (should not happen)")

    reg = ToolRegistry()
    reg.register(_DenyTool())

    hooks    = MagicMock()
    hooks.fire.return_value = []
    executor = ToolExecutor(registry=reg, hooks=hooks, settings=None)

    fake_call = {"id": "tc-003", "name": "_TestDenyTool", "input": {}}
    context   = ToolContext()
    state     = LoopState(iteration=1, stop_reason="continue", continuation_hint=None, token_budget_used=0, session_id="test")

    with patch.object(executor, "_ask_user_permission") as mock_ask:
        results = await executor.run_batch([fake_call], context=context)

    mock_ask.assert_not_called()
    assert results[0].is_error is True
    assert "denied" in results[0].content.lower()
