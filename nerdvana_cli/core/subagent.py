"""Subagent runner — spawns an isolated AgentLoop for a subtask."""

from __future__ import annotations

import asyncio

from nerdvana_cli.core.agent_loop import AgentLoop
from nerdvana_cli.core.analytics import CallOrigin
from nerdvana_cli.core.concurrency import DEFAULT_AGENT_SLOTS, agent_slot
from nerdvana_cli.core.subagent_config import SubagentConfig
from nerdvana_cli.core.tool import ConfirmCallback

_PROTOCOL_PREFIXES = (
    "\x00TOOL:",
    "\x00TOOL_DONE:",
    "\x00CTX_USAGE:",
    "\x00COMPACT:",
)


def label_confirm(confirm: ConfirmCallback | None, label: str) -> ConfirmCallback | None:
    """Wrap *confirm* so a request names the agent that is asking."""
    if confirm is None:
        return None

    async def _labelled(tool_name: str, message: str) -> bool:
        return await confirm(tool_name, f"[{label}] {message}")

    return _labelled


async def run_subagent(config: SubagentConfig, abort: asyncio.Event) -> tuple[str, int]:
    """Run an isolated AgentLoop and return (output_text, total_tokens).

    Protocol markers (tool status, context usage, compaction) are stripped
    so the parent receives only model-generated text.  ``total_tokens`` is
    the sum of input + output tokens recorded by the AgentLoop's usage tracker;
    it is 0 when no LLM calls were made (e.g. abort before first turn).
    """
    child_settings = config.settings.model_copy(deep=True)
    child_settings.session.max_turns = config.max_turns
    child_settings.goal.auto_verify  = False

    origin = CallOrigin(agent_id=config.agent_id, agent_type=config.name, category=config.category, parent_session_id=config.parent_session_id)
    loop   = AgentLoop(
        settings=child_settings, registry=config.registry, role_prompt=config.system_prompt, on_confirm=config.confirm, origin=origin,
        factories=config.factories,
    )
    loop.wrap_up_at = max(2, int(config.max_turns * config.wrap_up_fraction)) if config.wrap_up_fraction > 0 else 0
    parts: list[str] = []

    limit = getattr(child_settings.session, "max_parallel_agents", DEFAULT_AGENT_SLOTS)
    try:
        async with agent_slot(child_settings.model.provider, limit):
            async for chunk in loop.run(config.prompt):
                if abort.is_set():
                    return "".join(parts) + "\n[aborted]", 0
                if not any(chunk.startswith(p) for p in _PROTOCOL_PREFIXES):
                    parts.append(chunk)
    finally:
        config.cost_usd = loop.session_cost_usd()
        if config.absorb is not None:
            config.absorb(loop.usage_summary(), loop.signal_summary())

    config.stopped_for = loop.last_stop
    if loop.last_stop == "max_cost":
        parts.append(f"\n[Stopped: this agent used its share of the cost budget (${config.cost_usd:.4f}); the result above is partial.]")
    totals = loop.usage_summary()
    return "".join(parts), totals["input_tokens"] + totals["output_tokens"]

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
