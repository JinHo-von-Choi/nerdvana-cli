"""Subagent runner — spawns an isolated AgentLoop for a subtask."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from nerdvana_cli.core.agent_loop import AgentLoop
from nerdvana_cli.core.settings import NerdvanaSettings
from nerdvana_cli.core.tool import ToolRegistry

_PROTOCOL_PREFIXES = (
    "\x00TOOL:",
    "\x00TOOL_DONE:",
    "\x00CTX_USAGE:",
    "\x00COMPACT:",
)


@dataclass
class SubagentConfig:
    agent_id:  str
    name:      str
    prompt:    str
    settings:  NerdvanaSettings
    registry:  ToolRegistry
    max_turns: int = 50


async def run_subagent(config: SubagentConfig, abort: asyncio.Event) -> str:
    """Run an isolated AgentLoop and return captured text output.

    Protocol markers (tool status, context usage, compaction) are stripped
    so the parent receives only model-generated text.
    """
    child_settings = config.settings.model_copy(deep=True)
    child_settings.session.max_turns = config.max_turns

    loop   = AgentLoop(settings=child_settings, registry=config.registry)
    parts: list[str] = []

    async for chunk in loop.run(config.prompt):
        if abort.is_set():
            return "".join(parts) + "\n[aborted]"
        if not any(chunk.startswith(p) for p in _PROTOCOL_PREFIXES):
            parts.append(chunk)

    return "".join(parts)
