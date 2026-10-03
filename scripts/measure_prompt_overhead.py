"""measure_prompt_overhead.py: how many input tokens every request carries before the conversation.

Author: 최진호
Date:   2026-10-03

Every request to the model starts with the system prompt and the tool declarations. This
script builds both for the default tool set and prints their estimated size, the largest
system prompt sections and the largest tool declarations. The estimate is the same one the
agent loop uses (``core/context/token_estimator.py``), not a provider's own count.

The figures depend on the tool set and on the project documents found in ``--cwd``
(NIRNA.md, AGENTS.md and the like), so quote them together with the directory they were
measured in. Run it in an empty directory for the figure that does not depend on a project.

Usage::

    python scripts/measure_prompt_overhead.py [--cwd DIR] [--top N] [--json]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

from nerdvana_cli.core.agent_loop import AgentLoop
from nerdvana_cli.core.config.settings import NerdvanaSettings
from nerdvana_cli.core.context.token_estimator import approx_tokens
from nerdvana_cli.tools.registry import create_tool_registry

_SECTION = re.compile(r"\n(?=#{1,3} )")


def declarations(tools: list[Any]) -> list[dict[str, Any]]:
    """The tool declarations as a provider receives them."""
    return [{"name": t.name, "description": t.description_text, "input_schema": t.input_schema} for t in tools]


def measure(cwd: str) -> dict[str, Any]:
    """Token estimates of the system prompt and the tool declarations for the default tools in *cwd*."""
    settings     = NerdvanaSettings()
    settings.cwd = cwd
    registry     = create_tool_registry(settings=settings)
    loop         = AgentLoop(settings=settings, registry=registry)
    tools        = [t for t in registry.all_tools() if loop.policy.is_visible(t.name)]
    prompt       = loop.build_system_prompt()
    sections     = [(approx_tokens(part), part.splitlines()[0][:60]) for part in _SECTION.split(prompt) if part.strip()]
    declared     = [(approx_tokens(json.dumps(d, ensure_ascii=False)), d["name"]) for d in declarations(tools)]
    return {
        "cwd":                   cwd,
        "tools":                 len(tools),
        "system_prompt_tokens":  approx_tokens(prompt),
        "tool_declaration_tokens": sum(tokens for tokens, _ in declared),
        "sections":              sorted(sections, reverse=True),
        "declarations":          sorted(declared, reverse=True),
    }


def render(report: dict[str, Any], top: int) -> str:
    """The report as text."""
    total  = report["system_prompt_tokens"] + report["tool_declaration_tokens"]
    lines  = [
        f"directory               {report['cwd']}",
        f"tools                   {report['tools']}",
        f"system prompt           {report['system_prompt_tokens']:>7,} tokens",
        f"tool declarations       {report['tool_declaration_tokens']:>7,} tokens",
        f"fixed input per request {total:>7,} tokens",
        "",
        f"largest system prompt sections (top {top})",
        *(f"  {tokens:>6,}  {title}" for tokens, title in report["sections"][:top]),
        "",
        f"largest tool declarations (top {top})",
        *(f"  {tokens:>6,}  {name}" for tokens, name in report["declarations"][:top]),
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Measure the fixed input tokens of every request.")
    parser.add_argument("--cwd", type=Path, default=Path.cwd(), help="directory whose project documents are loaded (default: current)")
    parser.add_argument("--top", type=int, default=8)
    parser.add_argument("--json", action="store_true", help="print the report as JSON")
    options = parser.parse_args(sys.argv[1:] if argv is None else argv)
    report  = measure(str(options.cwd.resolve()))
    print(json.dumps(report, indent=2) if options.json else render(report, options.top))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
