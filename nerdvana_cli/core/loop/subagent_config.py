"""What a sub-agent runs with, and the factories a loop takes from outside core.

core may not import tools, so the sub-agent runner, the registry a sub-agent is
given and the ToolSearch tool reach the loop as factories from the composition
root (``cli/bootstrap.py``). A loop without them never defers tools behind
ToolSearch and never drafts a plan with a sub-agent.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from nerdvana_cli.core.config.settings import NerdvanaSettings
from nerdvana_cli.core.context.tool_index import ToolIndex
from nerdvana_cli.core.tool import BaseTool, ConfirmCallback, ToolRegistry


@dataclass
class SubagentConfig:
    agent_id:  str
    name:      str
    prompt:    str
    settings:  NerdvanaSettings
    registry:      ToolRegistry
    max_turns:     int = 50
    system_prompt: str = ""
    confirm:       ConfirmCallback | None = None
    category:      str = ""
    parent_session_id: str = ""
    # Fraction of max_turns after which the agent is told to wrap up and answer (0 = never).
    wrap_up_fraction: float = 0.6
    # Set by run_subagent: what the agent spent (USD) and why it stopped.
    cost_usd:      float = 0.0
    stopped_for:   str   = ""
    # Called with the agent's token totals and signal counts when it finishes, however it ended: the parent adds them to its own.
    absorb:        Callable[[dict[str, int], dict[str, int]], None] | None = None
    # The factories the agent's own loop is built with.
    factories:     LoopFactories | None = None


SubagentRunner          = Callable[[SubagentConfig, asyncio.Event], Awaitable[tuple[str, int]]]
SubagentRegistryFactory = Callable[..., ToolRegistry]
ToolSearchFactory       = Callable[[ToolIndex], BaseTool[Any]]


@dataclass(frozen=True)
class LoopFactories:
    """Builders the loop needs from outside core: sub-agents, their tools, and the ToolSearch tool."""

    run_subagent:      SubagentRunner | None          = None
    subagent_registry: SubagentRegistryFactory | None = None
    tool_search:       ToolSearchFactory | None       = None
