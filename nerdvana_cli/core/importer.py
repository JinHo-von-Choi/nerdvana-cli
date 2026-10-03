"""Bringing over what you already set up in another coding agent.

Author: 최진호
Date:   2026-10-03

Claude Code and Codex keep slash commands as markdown files and, for Claude Code, permission rules in
``settings.json``. This module finds them and converts what has an equivalent here:

* slash commands (``.claude/commands``, ``~/.claude/commands``, ``~/.codex/prompts``) become files in
  ``.nerdvana/commands`` (see ``core/user_commands.py``); an existing file is never replaced;
* ``permissions.allow`` and ``permissions.deny`` rules of a Claude Code ``settings.json`` become
  ``always_allow`` and ``always_deny`` rules, with ``Tool(prefix:*)`` turned into ``Tool(prefix *)``.

Nothing is written to the configuration file: the rules are returned for the user to read and paste.
Instruction files (``CLAUDE.md``, ``AGENTS.md``) and ``.claude/skills`` are read in place already.
"""

from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

_PREFIX_RULE = re.compile(r"^(?P<tool>[^()]+)\((?P<body>.*):\*\)$")
_NAME        = re.compile(r"^[A-Za-z0-9_.:-]+$")


@dataclass
class ImportPlan:
    """What an import found and would do."""

    commands: list[tuple[Path, Path]] = field(default_factory=list)   # (source, destination)
    skipped:  list[tuple[Path, str]]  = field(default_factory=list)   # (source, reason)
    allow:    list[str]               = field(default_factory=list)
    deny:     list[str]               = field(default_factory=list)
    notes:    list[str]               = field(default_factory=list)


def convert_rule(rule: str) -> str:
    """A Claude Code permission rule in this tool's syntax: ``Bash(git diff:*)`` becomes ``Bash(git diff *)``."""
    match = _PREFIX_RULE.match(rule.strip())
    if match:
        return f"{match['tool'].strip()}({match['body']} *)"
    return rule.strip()


def read_permissions(settings_file: Path) -> tuple[list[str], list[str]]:
    """The allow and deny rules of a Claude Code settings file, converted; empty when there is none."""
    try:
        data = json.loads(settings_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return [], []
    permissions = data.get("permissions") if isinstance(data, dict) else None
    if not isinstance(permissions, dict):
        return [], []
    return _rules(permissions, "allow"), _rules(permissions, "deny")


def _rules(permissions: dict[str, object], key: str) -> list[str]:
    values = permissions.get(key)
    return [convert_rule(r) for r in values if isinstance(r, str) and r.strip()] if isinstance(values, list) else []


def _command_sources(root: Path, home: Path, source: str) -> list[Path]:
    if source == "claude":
        return [home / ".claude" / "commands", root / ".claude" / "commands"]
    return [home / ".codex" / "prompts"]


def plan_import(source: str, root: Path, home: Path) -> ImportPlan:
    """Find the commands and rules of *source* (``claude`` or ``codex``) for the project in *root*."""
    plan        = ImportPlan()
    destination = root / ".nerdvana" / "commands"
    seen: set[str] = set()
    for directory in _command_sources(root, home, source):
        for file in sorted(directory.rglob("*.md")) if directory.is_dir() else []:
            name = ":".join(file.relative_to(directory).with_suffix("").parts)
            if not _NAME.match(name):
                plan.skipped.append((file, f"'{name}' is not a usable command name"))
            elif name in seen:
                plan.skipped.append((file, f"'{name}' already comes from another directory"))
            elif (destination / file.relative_to(directory)).exists():
                plan.skipped.append((file, "a command with this name exists here already"))
            else:
                seen.add(name)
                plan.commands.append((file, destination / file.relative_to(directory)))
    if source == "claude":
        for settings_file in (home / ".claude" / "settings.json", root / ".claude" / "settings.json", root / ".claude" / "settings.local.json"):
            allow, deny = read_permissions(settings_file)
            plan.allow += [r for r in allow if r not in plan.allow]
            plan.deny  += [r for r in deny if r not in plan.deny]
    if source == "claude" and (root / "CLAUDE.md").is_file():
        plan.notes.append("CLAUDE.md is read in place; nothing to import.")
    if source == "claude" and (root / ".claude" / "skills").is_dir():
        plan.notes.append("Skills in .claude/skills are read when skills.include_claude_skills is true.")
    return plan


def apply_commands(plan: ImportPlan) -> int:
    """Copy the planned commands; returns how many were written. An existing file is left as it is."""
    written = 0
    for source, destination in plan.commands:
        if destination.exists():
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        written += 1
    return written
