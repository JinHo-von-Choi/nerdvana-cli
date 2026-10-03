"""The planning gate: a read-only sub-agent drafts a plan before a complex prompt is worked on.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

import asyncio
import re

from nerdvana_cli.core.settings import NerdvanaSettings
from nerdvana_cli.core.subagent_config import LoopFactories, SubagentConfig

_COMPLEXITY_SIGNALS: list[str] = [
    r"리팩터링|refactor", r"새로운\s+(기능|모듈|서비스|시스템)|new\s+(feature|module|service|system)",
    r"마이그레이션|migration", r"\d+개\s+(파일|클래스|모듈)|\d+\s+(files?|classes?|modules?)",
    r"아키텍처|architecture|전면\s+개편", r"처음부터|from\s+scratch",
]


def _needs_planning(prompt: str) -> bool:
    return sum(1 for p in _COMPLEXITY_SIGNALS if re.search(p, prompt, re.IGNORECASE)) >= 2


async def plan_for(prompt: str, settings: NerdvanaSettings, factories: LoopFactories) -> str:
    """The plan drafted for *prompt* when ``session.planning_gate`` is on and the prompt looks complex, else empty."""
    if settings.session.planning_gate and _needs_planning(prompt):
        return await draft_plan(prompt, settings, factories)
    return ""


async def draft_plan(prompt: str, settings: NerdvanaSettings, factories: LoopFactories) -> str:
    """The plan a read-only sub-agent drafts for *prompt*; empty when *factories* cannot start sub-agents."""
    run_subagent, registry_for = factories.run_subagent, factories.subagent_registry
    if run_subagent is None or registry_for is None:
        return ""
    child = settings.model_copy(deep=True)
    child.session.planning_gate = False
    reg = registry_for(settings=child, allowed_tools=["Glob", "Grep", "FileRead", "Bash"])
    cfg = SubagentConfig(agent_id="plan_agent", name="Plan", max_turns=20,
                         prompt=f"Create an implementation plan for the following task:\n\n{prompt}",
                         settings=child, registry=reg, factories=factories)
    try:
        output, _ = await run_subagent(cfg, asyncio.Event())
        return output
    except Exception as exc:  # noqa: BLE001
        return f"[plan agent error] {exc}"
