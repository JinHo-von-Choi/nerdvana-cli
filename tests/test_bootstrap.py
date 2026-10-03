"""The composition root: each front end's profile reaches the loop it builds unchanged.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

import pytest

from nerdvana_cli.agents.builtin import BUILTIN_AGENTS
from nerdvana_cli.cli.bootstrap import ExecutionProfile, build_agent_loop, build_subagent
from nerdvana_cli.core.agent_loop import AgentLoop
from nerdvana_cli.core.session import SessionStorage
from nerdvana_cli.core.settings import NerdvanaSettings
from nerdvana_cli.core.subagent import run_subagent
from nerdvana_cli.core.task_state import TaskRegistry
from nerdvana_cli.core.tool import BaseTool
from nerdvana_cli.types import ToolResult


class _Ping(BaseTool[Any]):
    name             = "mcp__x__ping"
    description_text = "ping"
    tags: ClassVar[frozenset[str]] = frozenset({"mcp"})

    async def call(self, args: Any, context: Any, can_use_tool: Any = None, on_progress: Any = None) -> ToolResult:
        return ToolResult(tool_use_id="", content="pong")


def _settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> NerdvanaSettings:
    monkeypatch.setenv("NERDVANA_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setattr(AgentLoop, "create_provider_from_settings", lambda self: None)
    settings     = NerdvanaSettings()
    settings.cwd = str(tmp_path)
    return settings


def test_the_loop_gets_the_profiles_session_tasks_and_callbacks(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    async def _confirm(tool_name: str, message: str) -> bool:
        return True

    async def _ask(question: str, options: list[str]) -> str:
        return ""

    def _activity(state: Any) -> None:
        return None

    tasks   = TaskRegistry()
    session = SessionStorage(session_id="boot", storage_dir=str(tmp_path / "s"))
    loop    = build_agent_loop(_settings(monkeypatch, tmp_path), ExecutionProfile(
        session=session, task_registry=tasks, on_activity_change=_activity, on_ask_user=_ask, on_confirm=_confirm,
    ))
    context = loop._new_tool_context()
    assert loop.session is session
    assert context.task_registry is tasks and context.confirm is _confirm and context.ask_user is _ask
    assert loop._on_activity_change is _activity
    assert context.state["loop_factories"].run_subagent is run_subagent


def test_the_registry_holds_the_built_in_tools_and_the_profiles_mcp_tools(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    loop  = build_agent_loop(_settings(monkeypatch, tmp_path), ExecutionProfile(mcp_tools=[_Ping()]))
    names = {tool.name for tool in loop.registry.all_tools()}
    assert {"Bash", "FileRead", "Agent", "Swarm", "mcp__x__ping"} <= names


def test_a_sub_agent_gets_only_the_tools_its_definition_allows() -> None:
    definition = next(d for d in BUILTIN_AGENTS if d.agent_type == "code-reviewer")
    config     = build_subagent(NerdvanaSettings(), definition, "review this", agent_id="review", category="review")
    names      = {tool.name for tool in config.registry.all_tools()}
    assert config.name == "code-reviewer" and config.max_turns == definition.max_turns
    assert config.system_prompt == definition.system_prompt and config.category == "review"
    assert "FileWrite" not in names and "Agent" not in names
    assert config.factories is not None and config.factories.run_subagent is run_subagent
