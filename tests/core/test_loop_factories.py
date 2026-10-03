"""The factories a loop takes from the composition root: ToolSearch, the plan agent, and sub-agents.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, ClassVar
from unittest.mock import patch

import pytest

from nerdvana_cli.cli.bootstrap import loop_factories
from nerdvana_cli.core.agent_loop import AgentLoop
from nerdvana_cli.core.plan_gate import draft_plan, plan_for
from nerdvana_cli.core.session import SessionStorage
from nerdvana_cli.core.settings import NerdvanaSettings
from nerdvana_cli.core.subagent import run_subagent
from nerdvana_cli.core.subagent_config import LoopFactories, SubagentConfig
from nerdvana_cli.core.swarm import SwarmConfig, SwarmTask, run_swarm
from nerdvana_cli.core.task_state import TaskRegistry
from nerdvana_cli.core.tool import BaseTool, ToolRegistry
from nerdvana_cli.tools.subagent_registry import create_subagent_registry
from nerdvana_cli.tools.tool_search import ToolSearchTool
from nerdvana_cli.types import ToolResult


class _Mcp(BaseTool[Any]):
    tags: ClassVar[frozenset[str]] = frozenset({"mcp"})

    def __init__(self, name: str) -> None:
        self.name             = name
        self.description_text = f"{name} does a thing"
        self.input_schema     = {"type": "object", "properties": {}}

    async def call(self, args: Any, context: Any, can_use_tool: Any = None, on_progress: Any = None) -> ToolResult:
        return ToolResult(tool_use_id="", content="ran")


def _loop(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, factories: LoopFactories | None, **session: Any) -> AgentLoop:
    monkeypatch.setenv("NERDVANA_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setattr(AgentLoop, "create_provider_from_settings", lambda self: None)
    registry = ToolRegistry()
    registry.register(_Mcp("mcp__a__one"))
    settings     = NerdvanaSettings()
    settings.cwd = str(tmp_path)
    for key, value in session.items():
        setattr(settings.session, key, value)
    storage = SessionStorage(session_id="factories", storage_dir=str(tmp_path / "s"))
    return AgentLoop(settings=settings, registry=registry, session=storage, factories=factories)


def test_the_composition_root_hands_over_the_runner_the_registry_and_tool_search() -> None:
    factories = loop_factories()
    assert factories.run_subagent is run_subagent
    assert factories.subagent_registry is create_subagent_registry
    assert factories.tool_search is ToolSearchTool


def test_a_loop_with_a_tool_search_factory_defers_and_registers_the_search_tool(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    loop  = _loop(monkeypatch, tmp_path, loop_factories(), defer_tools="always")
    names = [tool.name for tool in loop._prepare_tools()]
    assert "ToolSearch" in names
    assert loop._declared(loop._prepare_tools()) == [loop.registry.get("ToolSearch")]


def test_a_loop_without_a_tool_search_factory_never_defers(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    loop  = _loop(monkeypatch, tmp_path, None, defer_tools="always")
    tools = loop._prepare_tools()
    assert [tool.name for tool in tools] == ["mcp__a__one"]
    assert loop._declared(tools) == tools


async def test_the_plan_agent_runs_through_the_injected_runner_with_a_read_only_registry() -> None:
    seen: list[SubagentConfig] = []

    async def _runner(config: SubagentConfig, abort: asyncio.Event) -> tuple[str, int]:
        seen.append(config)
        return "1. read\n2. change", 0

    factories = LoopFactories(run_subagent=_runner, subagent_registry=create_subagent_registry)
    settings  = NerdvanaSettings()
    settings.session.planning_gate = True
    assert await draft_plan("refactor the parser", settings, factories) == "1. read\n2. change"
    (config,) = seen
    assert config.name == "Plan" and config.factories is factories
    assert config.settings.session.planning_gate is False and settings.session.planning_gate is True
    assert sorted(tool.name for tool in config.registry.all_tools()) == ["Bash", "FileRead", "Glob", "Grep"]


async def test_without_a_runner_there_is_no_plan() -> None:
    assert await draft_plan("refactor the parser", NerdvanaSettings(), LoopFactories()) == ""


async def test_the_gate_drafts_a_plan_only_for_a_complex_prompt_when_it_is_on() -> None:
    async def _runner(config: SubagentConfig, abort: asyncio.Event) -> tuple[str, int]:
        return "plan", 0

    factories = LoopFactories(run_subagent=_runner, subagent_registry=create_subagent_registry)
    settings  = NerdvanaSettings()
    complex_  = "refactor the architecture from scratch"
    settings.session.planning_gate = False
    assert await plan_for(complex_, settings, factories) == ""
    settings.session.planning_gate = True
    assert await plan_for("rename this function", settings, factories) == ""
    assert await plan_for(complex_, settings, factories) == "plan"


def test_the_tool_context_carries_the_factories_to_agent_and_swarm_tools(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    factories = loop_factories()
    loop      = _loop(monkeypatch, tmp_path, factories)
    assert loop._new_tool_context().state["loop_factories"] is factories


async def test_swarm_workers_are_built_with_the_leaders_factories() -> None:
    factories = loop_factories()
    seen: list[LoopFactories | None] = []

    async def _fake(config: SubagentConfig, abort: asyncio.Event) -> tuple[str, int]:
        seen.append(config.factories)
        return "done", 0

    config = SwarmConfig(
        team_name     = "t",
        tasks         = [SwarmTask(name="w1", prompt="p1"), SwarmTask(name="w2", prompt="p2")],
        settings      = NerdvanaSettings(),
        task_registry = TaskRegistry(),
        factories     = factories,
    )
    with patch("nerdvana_cli.core.swarm.run_subagent", side_effect=_fake):
        await run_swarm(config, create_subagent_registry)
    assert seen == [factories, factories]
