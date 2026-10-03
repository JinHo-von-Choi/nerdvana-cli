"""nerdvana review: a read-only review of a change, started from the code the change touches.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
from typing import Any

from nerdvana_cli.core.review_context import MAX_DIFF_CHARS, ReviewError, build_context, render_prompt

SEVERITIES = ("low", "medium", "high")
FAIL_ON    = ("never", *SEVERITIES)


def parse_findings(text: str) -> list[dict[str, Any]]:
    """The findings of the last JSON object in the reviewer's answer that has a ``findings`` list."""
    for match in reversed(list(re.finditer(r"\{", text))):
        try:
            data, _ = json.JSONDecoder().raw_decode(text[match.start():])
        except ValueError:
            continue
        if isinstance(data, dict) and isinstance(data.get("findings"), list):
            return [f for f in data["findings"] if isinstance(f, dict)]
    return []


def should_fail(findings: list[dict[str, Any]], fail_on: str) -> bool:
    """True when a finding is at or above the *fail_on* severity (never: always False)."""
    if fail_on == "never":
        return False
    limit = SEVERITIES.index(fail_on)
    return any(str(f.get("severity", "low")).lower() in SEVERITIES[limit:] for f in findings)


def render_findings(findings: list[dict[str, Any]]) -> str:
    """The findings as text, most severe first."""
    if not findings:
        return "No findings."
    order = {name: index for index, name in enumerate(reversed(SEVERITIES))}
    lines = []
    for f in sorted(findings, key=lambda f: order.get(str(f.get("severity", "low")).lower(), 9)):
        lines.append(f"[{str(f.get('severity', '?')).lower()}] {f.get('file', '?')}:{f.get('line', '?')}  {f.get('problem', '')}")
        if f.get("evidence"):
            lines.append(f"        {f['evidence']}")
    return "\n".join(lines)


async def _run_reviewer(prompt: str, model: str, provider: str) -> str:
    """Run the read-only code-reviewer agent on *prompt* and return its answer."""
    from nerdvana_cli.agents.builtin import BUILTIN_AGENTS
    from nerdvana_cli.cli.runtime import resolve_run_provider
    from nerdvana_cli.core.managed_policy import ManagedPolicyError
    from nerdvana_cli.core.model_routing import apply_model_spec, select_model
    from nerdvana_cli.core.settings import NerdvanaSettings
    from nerdvana_cli.core.subagent import SubagentConfig, run_subagent
    from nerdvana_cli.tools.registry import create_subagent_registry

    settings     = NerdvanaSettings.load()
    settings.cwd = os.getcwd()
    if provider:
        settings.model.provider = provider
    if model:
        settings.model.model = model
    else:
        apply_model_spec(settings, select_model("", "review", "", "", settings.agents.categories))
    name, key_missing = resolve_run_provider(settings)
    if key_missing:
        raise ReviewError(f"No API key found for {name}.")
    try:
        settings.managed_policy.enforce(settings)
    except ManagedPolicyError as exc:
        raise ReviewError(str(exc)) from exc
    definition = next(d for d in BUILTIN_AGENTS if d.agent_type == "code-reviewer")
    registry   = create_subagent_registry(settings=settings, allowed_tools=definition.allowed_tools)
    config     = SubagentConfig(
        agent_id="review", name="code-reviewer", prompt=prompt, settings=settings, registry=registry,
        max_turns=definition.max_turns, system_prompt=definition.system_prompt, category="review",
    )
    output, _ = await run_subagent(config, asyncio.Event())
    return output


def review_command(base: str, paths: list[str], context_only: bool, output_format: str, fail_on: str,
                   model: str, provider: str, max_diff_chars: int = MAX_DIFF_CHARS) -> int:
    """Run the review and return the exit code: 0 done, 1 a finding at or above ``fail_on``, 2 cannot run."""
    try:
        diff, symbols, references = build_context(os.getcwd(), base, paths or None)
        if not diff.strip():
            print("No changes against " + base + ".", file=sys.stderr)
            return 0
        prompt = render_prompt(base, diff, symbols, references, max_diff_chars)
        if context_only:
            print(prompt)
            return 0
        findings = parse_findings(asyncio.run(_run_reviewer(prompt, model, provider)))
    except ReviewError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    failed = should_fail(findings, fail_on)
    if output_format == "json":
        print(json.dumps({"type": "review", "base": base, "findings": findings, "failed": failed}, indent=2))
    else:
        print(render_findings(findings))
    return 1 if failed else 0
