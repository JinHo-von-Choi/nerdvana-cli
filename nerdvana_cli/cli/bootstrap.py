"""Composition root: the one place that builds the tool registry and the agent loop.

core may not import tools, so whatever the loop builds from the tools package
reaches it from here. Each front end (``nerdvana run``, the TUI, ``nerdvana
review``) describes what is its own in an ``ExecutionProfile`` or an agent
definition; the permission mode travels in the settings as before.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from nerdvana_cli.agents.registry import AgentDefinition
from nerdvana_cli.core.activity_state import ActivityState
from nerdvana_cli.core.agent_loop import AgentLoop
from nerdvana_cli.core.config.settings import NerdvanaSettings
from nerdvana_cli.core.state.session import SessionStorage
from nerdvana_cli.core.subagent import run_subagent
from nerdvana_cli.core.subagent_config import LoopFactories, SubagentConfig
from nerdvana_cli.core.task_state import TaskRegistry
from nerdvana_cli.core.tool import AskUserCallback, ConfirmCallback
from nerdvana_cli.tools.registry import create_tool_registry
from nerdvana_cli.tools.subagent_registry import create_subagent_registry
from nerdvana_cli.tools.tool_search import ToolSearchTool


@dataclass(frozen=True)
class ExecutionProfile:
    """What a front end decides about the agent it starts: its session, its extra tools and who answers it."""

    session:            SessionStorage | None                    = None
    task_registry:      TaskRegistry | None                      = None
    parism_client:      Any                                      = None
    mcp_tools:          list[Any]                                = field(default_factory=list)
    on_activity_change: Callable[[ActivityState], None] | None   = None
    on_ask_user:        AskUserCallback | None                   = None
    on_confirm:         ConfirmCallback | None                   = None
    on_thinking_chunk:  Callable[[str], None] | None             = None


def loop_factories() -> LoopFactories:
    """The sub-agent runner, the sub-agent registry and the ToolSearch tool, for a loop to build with."""
    return LoopFactories(run_subagent=run_subagent, subagent_registry=create_subagent_registry, tool_search=ToolSearchTool)


def build_agent_loop(settings: NerdvanaSettings, profile: ExecutionProfile) -> AgentLoop:
    """The full tool registry for *settings* and the top-level agent loop that works with it."""
    registry = create_tool_registry(
        parism_client = profile.parism_client,
        mcp_tools     = profile.mcp_tools,
        settings      = settings,
        task_registry = profile.task_registry,
    )
    return AgentLoop(
        settings           = settings,
        registry           = registry,
        session            = profile.session,
        task_registry      = profile.task_registry,
        on_activity_change = profile.on_activity_change,
        on_ask_user        = profile.on_ask_user,
        on_confirm         = profile.on_confirm,
        on_thinking_chunk  = profile.on_thinking_chunk,
        factories          = loop_factories(),
    )


def build_subagent(settings: NerdvanaSettings, definition: AgentDefinition, prompt: str, agent_id: str, category: str) -> SubagentConfig:
    """A sub-agent of the type *definition* with only the tools it allows, ready for ``run_subagent``."""
    return SubagentConfig(
        agent_id      = agent_id,
        name          = definition.agent_type,
        prompt        = prompt,
        settings      = settings,
        registry      = create_subagent_registry(settings=settings, allowed_tools=definition.allowed_tools),
        max_turns     = definition.max_turns,
        system_prompt = definition.system_prompt,
        category      = category,
        factories     = loop_factories(),
    )
