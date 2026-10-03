"""Composition root: the one place that wires the agent loop to the tools.

core may not import tools, so whatever the loop builds from the tools package
reaches it from here.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

from nerdvana_cli.core.subagent import run_subagent
from nerdvana_cli.core.subagent_config import LoopFactories
from nerdvana_cli.tools.subagent_registry import create_subagent_registry
from nerdvana_cli.tools.tool_search import ToolSearchTool


def loop_factories() -> LoopFactories:
    """The sub-agent runner, the sub-agent registry and the ToolSearch tool, for a loop to build with."""
    return LoopFactories(run_subagent=run_subagent, subagent_registry=create_subagent_registry, tool_search=ToolSearchTool)
