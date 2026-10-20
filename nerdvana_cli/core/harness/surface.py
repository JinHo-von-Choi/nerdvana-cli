"""The eight-component harness surface and the Omitted-is-Inherited tree validator.

The surface of a run is what surrounds the model: the system prompt (c1), the
guardrails (c2), the memory (c3), the tool guidance (c4), the subagents (c5),
the skills (c6), the helper scripts (c7) and the hooks (c8). Each of them is a
file on disk, and the run sees them as one tree.

Two rules hold over that tree. A delta that omits a path inherits it: only the
paths a run emits change, and everything else stays as it was. And the
invariants of the surface — the guardrails — are not editable at all: a
candidate tree that changed one is rejected, however it was produced.

Based on Google Research Harness2 (arXiv:2610.14224).

Author: 최진호
Date:   2026-10-20
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# The surface of a harness, as a mapping of relative path to file content.
HarnessTree = dict[str, str]

# A skill in the directory layout: .nerdvana/skills/<name>/SKILL.md
_SKILL_RX = re.compile(r"^\.nerdvana/skills/([a-z0-9][a-z0-9_\-]*)/SKILL\.md$")

# A skill in the flat layout: .nerdvana/skills/<name>.md
_FLAT_SKILL_RX = re.compile(r"^\.nerdvana/skills/([a-z0-9][a-z0-9_\-]*)\.md$")

# A subagent definition: .nerdvana/agents/<name>.yml
_SUBAGENT_RX = re.compile(r"^\.nerdvana/agents/([a-z0-9][a-z0-9_\-]*)\.ya?ml$")

# A helper script a skill may call: scripts/<name>.py
_SCRIPT_RX = re.compile(r"^scripts/[a-z0-9_][a-z0-9_\-]*\.py$")

# A call to a helper script inside a skill.
_SCRIPT_REF_RX = re.compile(r"python3?\s+(scripts/[a-z0-9_\-]+\.py)")


@dataclass(frozen=True)
class SurfaceSpec:
    """The editable surface of a harness, across the c1..c8 components."""

    name:                 str
    always_on:            tuple[str, ...]
    immutable_invariants: tuple[str, ...]
    always_on_max_chars:  int
    autotrigger_file:     str
    hooks_file:           str


NERDVANA_SURFACE = SurfaceSpec(
    name                = "nerdvana",
    always_on           = ("NIRNA.md", "CLAUDE.md"),
    immutable_invariants = (".iron-laws.yml",),
    always_on_max_chars = 48_000,
    autotrigger_file    = "NIRNA.md",
    hooks_file          = ".nerdvana/hooks.yml",
)


def normalize_rel(rel: str) -> str:
    """The path as it is written in the tree; a path that escapes it is refused."""
    r = str(rel).strip().replace("\\", "/")
    if r.startswith("/") or ".." in r.split("/"):
        raise ValueError(f"Path escapes harness surface: {rel!r}")
    while r.startswith("./"):
        r = r[2:]
    return r.strip("/")


def is_editable_path(surface: SurfaceSpec, rel: str) -> bool:
    """True when *rel* is a path of the surface a run may write."""
    try:
        rel = normalize_rel(rel)
    except ValueError:
        return False
    if rel in surface.always_on or rel == surface.hooks_file:
        return True
    return bool(
        _SKILL_RX.fullmatch(rel)
        or _FLAT_SKILL_RX.fullmatch(rel)
        or _SUBAGENT_RX.fullmatch(rel)
        or _SCRIPT_RX.fullmatch(rel)
    )


def apply_delta_tree(surface: SurfaceSpec, base: HarnessTree, delta: HarnessTree) -> HarnessTree:
    """Omitted-is-inherited merge: only explicitly emitted paths update the tree.

    Time complexity: O(F) where F is the number of files in base + delta.
    """
    merged = dict(base)
    for raw_path, content in delta.items():
        rel = normalize_rel(raw_path)
        if not is_editable_path(surface, rel):
            raise ValueError(f"Path not on editable surface: {rel!r}")
        merged[rel] = content
    return merged


def skill_names(tree: HarnessTree) -> list[str]:
    """The names of the skills on the surface, sorted."""
    names: set[str] = set()
    for path in tree:
        match = _SKILL_RX.fullmatch(path) or _FLAT_SKILL_RX.fullmatch(path)
        if match:
            names.add(match.group(1))
    return sorted(names)


def autotrigger_skills(surface: SurfaceSpec, tree: HarnessTree) -> list[str]:
    """Ensure every skill on the surface is named in the autotrigger always-on file.

    A skill nobody names is a skill nobody reaches, so the ones that are missing
    are added to the always-on file and their names are returned.
    """
    target_text = tree.get(surface.autotrigger_file, "")
    added: list[str] = []
    for name in skill_names(tree):
        if re.search(rf"\b{re.escape(name)}\b", target_text) is None:
            added.append(name)
    if added:
        suffix = "\n## Auto-Triggered Skills\n" + "\n".join(f"- Use skill `{n}` when applicable." for n in added) + "\n"
        tree[surface.autotrigger_file] = target_text.rstrip() + "\n" + suffix
    return added


def validate_harness_tree(surface: SurfaceSpec, base: HarnessTree, candidate: HarnessTree) -> list[str]:
    """Validate the c1..c8 invariants, the always-on caps and the script references.

    Time complexity: O(F + M) where F is the file count and M the total character length.
    """
    errors: list[str] = []

    for inv_path in surface.immutable_invariants:
        if inv_path in base and candidate.get(inv_path) != base[inv_path]:
            errors.append(f"Immutable guardrail c2 modified: {inv_path}")

    auto_content = candidate.get(surface.autotrigger_file, "").strip()
    if not auto_content:
        errors.append(f"Always-on file {surface.autotrigger_file} must never be empty or deleted")

    always_on_chars = sum(len(candidate.get(p, "")) for p in surface.always_on)
    if always_on_chars > surface.always_on_max_chars:
        errors.append(
            f"Always-on context budget exceeded: {always_on_chars} > {surface.always_on_max_chars} chars"
        )

    for path, content in candidate.items():
        if _SKILL_RX.fullmatch(path) or _FLAT_SKILL_RX.fullmatch(path):
            for match in _SCRIPT_REF_RX.finditer(content):
                script_rel = match.group(1)
                if script_rel not in candidate:
                    errors.append(f"Skill {path} references missing helper script: {script_rel}")

    return errors
