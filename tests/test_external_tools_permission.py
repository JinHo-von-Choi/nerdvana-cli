"""Regression tests — ExternalProject tool check_permissions override is called.

Verifies C-5 fix:
- check_permissions (plural, correct name) is actually dispatched by tool_executor
- ListQueryableProjectsTool → ALLOW
- RegisterExternalProjectTool → ASK  (filesystem write)
- QueryExternalProjectTool → ASK     (subprocess spawn)
- The dead check_permission (singular) method no longer exists

작성자: 최진호
작성일: 2026-04-18
"""

from __future__ import annotations

import pytest

from nerdvana_cli.core.tool import ToolContext
from nerdvana_cli.tools.external_project_tools import (
    ListQueryableProjectsTool,
    QueryExternalProjectTool,
    RegisterExternalProjectTool,
)
from nerdvana_cli.types import PermissionBehavior


@pytest.fixture()
def ctx() -> ToolContext:
    return ToolContext()


# ---------------------------------------------------------------------------
# Direct check_permissions dispatch
# ---------------------------------------------------------------------------

def test_list_permission_is_allow(ctx: ToolContext) -> None:
    tool   = ListQueryableProjectsTool()
    result = tool.check_permissions(None, ctx)
    assert result.behavior == PermissionBehavior.ALLOW


def test_register_permission_is_ask(ctx: ToolContext) -> None:
    tool   = RegisterExternalProjectTool()
    # args value doesn't matter for the static permission decision
    result = tool.check_permissions(object(), ctx)
    assert result.behavior == PermissionBehavior.ASK


def test_query_permission_is_ask(ctx: ToolContext) -> None:
    tool   = QueryExternalProjectTool()
    result = tool.check_permissions(object(), ctx)
    assert result.behavior == PermissionBehavior.ASK


# ---------------------------------------------------------------------------
# Dead method check_permission (singular) must NOT exist
# ---------------------------------------------------------------------------

def test_dead_method_absent_list() -> None:
    assert not hasattr(ListQueryableProjectsTool, "check_permission"), (
        "Singular check_permission is a dead stub — it must be removed"
    )


def test_dead_method_absent_register() -> None:
    assert not hasattr(RegisterExternalProjectTool, "check_permission")


def test_dead_method_absent_query() -> None:
    assert not hasattr(QueryExternalProjectTool, "check_permission")


# ---------------------------------------------------------------------------
# Dead method get_permission_behavior must NOT exist
# ---------------------------------------------------------------------------

def test_dead_get_permission_behavior_absent_list() -> None:
    assert not hasattr(ListQueryableProjectsTool, "get_permission_behavior")


def test_dead_get_permission_behavior_absent_register() -> None:
    assert not hasattr(RegisterExternalProjectTool, "get_permission_behavior")


def test_dead_get_permission_behavior_absent_query() -> None:
    assert not hasattr(QueryExternalProjectTool, "get_permission_behavior")


# ---------------------------------------------------------------------------
# ToolExecutor integration — check_permissions result is honoured
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_executor_denies_ask_in_non_tty() -> None:
    """ToolExecutor must deny an ASK tool when stdin is not a TTY (CI/pipe)."""
    import sys
    from unittest.mock import AsyncMock, MagicMock, patch

    from nerdvana_cli.core.tool_executor import ToolExecutor
    from nerdvana_cli.core.tool import ToolRegistry
    from nerdvana_cli.core.loop_state import LoopState

    # Build a minimal registry with RegisterExternalProjectTool (ASK).
    reg  = ToolRegistry()
    tool = RegisterExternalProjectTool()
    reg.register(tool)

    hooks    = MagicMock()
    hooks.fire.return_value = []
    executor = ToolExecutor(registry=reg, hooks=hooks, settings=None)

    fake_call = {
        "id":    "test-id-001",
        "name":  "RegisterExternalProject",
        "input": {"name": "x", "path": "/tmp"},
    }
    context = ToolContext()
    state   = LoopState(iteration=1, stop_reason="continue", continuation_hint=None, token_budget_used=0, session_id="test")

    # Ensure stdin.isatty() returns False (non-interactive).
    with patch.object(sys.stdin, "isatty", return_value=False):
        results = await executor.run_batch([fake_call], context=context)

    assert len(results) == 1
    result = results[0]
    assert result.is_error is True
    assert "denied" in result.content.lower()


@pytest.mark.asyncio
async def test_executor_allows_ask_when_user_confirms() -> None:
    """ToolExecutor must proceed when user types 'y' at the ASK prompt (TTY)."""
    import sys
    from pathlib import Path
    from unittest.mock import AsyncMock, MagicMock, patch

    from nerdvana_cli.core.external_projects import ExternalProjectRegistry
    from nerdvana_cli.core.loop_state import LoopState
    from nerdvana_cli.core.tool import ToolRegistry
    from nerdvana_cli.core.tool_executor import ToolExecutor

    tmp_registry = ExternalProjectRegistry()
    reg  = ToolRegistry()
    tool = RegisterExternalProjectTool(registry=tmp_registry)
    reg.register(tool)

    hooks    = MagicMock()
    hooks.fire.return_value = []
    executor = ToolExecutor(registry=reg, hooks=hooks, settings=None)

    import tempfile
    with tempfile.TemporaryDirectory() as tmpdir:
        fake_call = {
            "id":    "test-id-002",
            "name":  "RegisterExternalProject",
            "input": {"name": "proj", "path": tmpdir},
        }
        context = ToolContext()
        state   = LoopState(iteration=1, stop_reason="continue", continuation_hint=None, token_budget_used=0, session_id="test")

        # Simulate interactive TTY with user typing "y".
        with (
            patch.object(sys.stdin, "isatty", return_value=True),
            patch("builtins.input", return_value="y"),
        ):
            results = await executor.run_batch([fake_call], context=context)

    assert len(results) == 1
    assert results[0].is_error is False, results[0].content
