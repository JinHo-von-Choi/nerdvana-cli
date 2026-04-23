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

def create_shared_context(
    messages: list[dict[str, str]],
    max_summary_tokens: int = 500,
) -> str:
    """Create summarized context for sharing between agents.

    Extracts key information from conversation history into
    a compact summary suitable for passing to other agents.

    Args:
        messages: Conversation messages to summarize
        max_summary_tokens: Maximum tokens for summary

    Returns:
        Summarized context string
    """
    if not messages:
        return ""

    intents: list[str] = []
    outcomes: list[str] = []

    for msg in messages:
        if msg.get("role") == "user":
            content = msg.get("content", "")
            first_sentence = content.split(".")[0].split("?")[0].strip()
            if first_sentence and len(first_sentence) > 10:
                intents.append(first_sentence[:100])
        elif msg.get("role") == "assistant":
            content = msg.get("content", "")
            if any(kw in content.lower() for kw in ["found", "created", "fixed", "updated", "deleted"]):
                outcomes.append(content[:100])

    parts = []
    if intents:
        parts.append(f"Goals: {'; '.join(intents[:3])}")
    if outcomes:
        parts.append(f"Results: {'; '.join(outcomes[:3])}")

    summary = " | ".join(parts)
    max_chars = max_summary_tokens * 4
    return summary[:max_chars]
