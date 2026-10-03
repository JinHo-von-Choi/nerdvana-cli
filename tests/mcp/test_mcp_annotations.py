"""What an MCP server's tool annotations change: parallel execution and confirmation.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

from typing import Any

import pytest

from nerdvana_cli.core.safety.policy import PermissionPolicy
from nerdvana_cli.core.tool import PermissionBehavior, PermissionResult, ToolCategory
from nerdvana_cli.mcp.tools import McpDestructiveToolAdapter, McpToolAdapter, build_mcp_tool


class _Client:
    server_name = "srv"


def _def(annotations: Any = None) -> dict[str, Any]:
    tool_def: dict[str, Any] = {"name": "act", "description": "d", "inputSchema": {"type": "object"}}
    if annotations is not None:
        tool_def["annotations"] = annotations
    return tool_def


def test_a_tool_with_no_annotations_is_serialized_and_not_flagged_destructive() -> None:
    tool = build_mcp_tool("srv", _def(), _Client())  # type: ignore[arg-type]
    assert type(tool) is McpToolAdapter
    assert tool.is_concurrency_safe is False
    assert tool.is_destructive is False
    assert tool.category == ToolCategory.WRITE


def test_only_a_read_only_tool_may_run_next_to_other_calls() -> None:
    read_only = build_mcp_tool("srv", _def({"readOnlyHint": True}), _Client())  # type: ignore[arg-type]
    other     = build_mcp_tool("srv", _def({"readOnlyHint": False, "idempotentHint": True}), _Client())  # type: ignore[arg-type]
    assert read_only.is_concurrency_safe is True
    assert other.is_concurrency_safe is False


def test_a_destructive_tool_asks_before_it_runs() -> None:
    tool = build_mcp_tool("srv", _def({"destructiveHint": True}), _Client())  # type: ignore[arg-type]
    assert isinstance(tool, McpDestructiveToolAdapter)
    assert tool.is_destructive is True and tool.category == ToolCategory.DESTRUCTIVE
    policy = PermissionPolicy(mode_name="default", trust_level="normal")
    decided = policy.decide(tool, PermissionResult(PermissionBehavior.ALLOW, ""))
    assert decided.behavior == PermissionBehavior.ASK


def test_read_only_wins_over_a_contradicting_destructive_hint() -> None:
    tool = build_mcp_tool("srv", _def({"readOnlyHint": True, "destructiveHint": True}), _Client())  # type: ignore[arg-type]
    assert type(tool) is McpToolAdapter
    assert tool.is_concurrency_safe is True and tool.is_destructive is False


@pytest.mark.parametrize("annotations", ["readOnlyHint", ["readOnlyHint"], {"readOnlyHint": "true"}, {"readOnlyHint": 1}])
def test_malformed_annotations_are_ignored(annotations: Any) -> None:
    tool = build_mcp_tool("srv", _def(annotations), _Client())  # type: ignore[arg-type]
    assert tool.is_concurrency_safe is False and tool.is_destructive is False
