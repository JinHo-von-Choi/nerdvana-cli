"""nerdvana import: bring over commands and permission rules from Claude Code or Codex.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

from pathlib import Path

from nerdvana_cli.cli.importer import ImportPlan, apply_commands, plan_import

SOURCES = ("claude", "codex")


def render(plan: ImportPlan, source: str, written: int | None) -> str:
    """The plan or the outcome as text, with the rules to paste."""
    lines = [f"Import from {source}:"]
    verb  = "would copy" if written is None else "copied"
    lines.append(f"  commands: {verb} {len(plan.commands) if written is None else written}")
    lines += [f"    {src}  ->  {dst}" for src, dst in plan.commands]
    lines += [f"  skipped {src}: {why}" for src, why in plan.skipped]
    if plan.allow or plan.deny:
        lines += ["", "Permission rules found (not applied; paste what you want into the configuration):", "", "permissions:"]
        if plan.allow:
            lines += ["  always_allow:", *(f'    - "{r}"' for r in plan.allow)]
        if plan.deny:
            lines += ["  always_deny:", *(f'    - "{r}"' for r in plan.deny)]
    lines += [f"  note: {n}" for n in plan.notes]
    if written is None and plan.commands:
        lines += ["", "Nothing was written. Run again with --write to copy the commands."]
    return "\n".join(lines)


def import_command(source: str, write: bool, project: str) -> str:
    """Plan (and with *write* apply) the import for the project in *project*; returns the text to print."""
    plan = plan_import(source, Path(project).resolve(), Path.home())
    return render(plan, source, apply_commands(plan) if write else None)
