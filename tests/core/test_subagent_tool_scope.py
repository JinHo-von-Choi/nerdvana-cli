"""Which of the session's tools a subagent receives.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from nerdvana_cli.agents.builtin import BUILTIN_AGENTS
from nerdvana_cli.core.config.settings import NerdvanaSettings
from nerdvana_cli.core.task_state import TaskRegistry
from nerdvana_cli.core.tool import BaseTool, ToolCategory, ToolContext
from nerdvana_cli.tools.agent_tool import AgentToolArgs
from nerdvana_cli.tools.registry import create_tool_registry
from nerdvana_cli.tools.subagent_registry import create_subagent_registry
from nerdvana_cli.types import ToolResult


class _Tool(BaseTool[Any]):
    description_text = "t"

    def __init__(self, name: str, category: ToolCategory) -> None:
        self.name     = name
        self.category = category  # type: ignore[misc]

    async def call(self, args: Any, context: Any, can_use_tool: Any = None, on_progress: Any = None) -> ToolResult:
        return ToolResult(tool_use_id="", content="")


PARENT = [
    _Tool("lsp_find_references", ToolCategory.READ),
    _Tool("find_symbol", ToolCategory.SYMBOLIC),
    _Tool("lsp_rename", ToolCategory.WRITE),
    _Tool("mcp__srv__write_row", ToolCategory.WRITE),
    _Tool("Agent", ToolCategory.META),
    _Tool("AskUser", ToolCategory.META),
    _Tool("restart_language_server", ToolCategory.META),
]


def _names(**kwargs: Any) -> set[str]:
    return {t.name for t in create_subagent_registry(parent_tools=PARENT, **kwargs).all_tools()}


def test_read_token_adds_only_read_and_symbolic_tools() -> None:
    names = _names(allowed_tools=["FileRead", "@read"])
    assert {"FileRead", "Glob", "Grep", "lsp_find_references", "find_symbol"} <= names
    assert "lsp_rename" not in names
    assert "mcp__srv__write_row" not in names
    assert "FileWrite" not in names
    assert "Bash" not in names


def test_wildcard_admits_writes_but_never_meta_tools() -> None:
    names = _names(allowed_tools=["*"])
    assert {"lsp_rename", "mcp__srv__write_row", "FileWrite", "Bash"} <= names
    assert not names & {"Agent", "AskUser", "restart_language_server"}


def test_named_write_tool_is_admitted_by_name() -> None:
    assert "mcp__srv__write_row" in _names(allowed_tools=["mcp__srv__write_row"])


def test_parent_instances_are_shared() -> None:
    registry = create_subagent_registry(parent_tools=PARENT, allowed_tools=["@read"])
    assert registry.get("lsp_find_references") is PARENT[0]


def test_read_only_builtin_agents_use_the_read_token() -> None:
    for agent_type in ("Explore", "Plan", "code-reviewer"):
        agent = next(a for a in BUILTIN_AGENTS if a.agent_type == agent_type)
        assert "@read" in agent.allowed_tools
        assert "FileWrite" not in agent.allowed_tools


@pytest.mark.asyncio
async def test_agent_tool_hands_parent_tools_to_the_child() -> None:
    settings = NerdvanaSettings()
    tasks    = TaskRegistry()
    registry = create_tool_registry(settings=settings, task_registry=tasks)
    registry.register(PARENT[1])
    agent    = registry.get("Agent")
    assert agent is not None

    with patch("nerdvana_cli.tools.agent_tool.run_subagent", new_callable=AsyncMock, return_value=("ok", 1)) as run:
        await agent.call(AgentToolArgs(prompt="look", subagent_type="Explore"), ToolContext(task_registry=tasks), None)

    child = {t.name for t in run.await_args.args[0].registry.all_tools()}
    assert "find_symbol" in child
    assert "WebFetch" in child
    assert "FileWrite" not in child
