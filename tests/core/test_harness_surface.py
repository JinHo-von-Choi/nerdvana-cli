"""Tests for the harness surface and its tree validator (Task 2.1).

Author: 최진호
Date:   2026-10-20
"""

from __future__ import annotations

import pytest

from nerdvana_cli.core.harness.surface import (
    NERDVANA_SURFACE,
    apply_delta_tree,
    autotrigger_skills,
    is_editable_path,
    normalize_rel,
    skill_names,
    validate_harness_tree,
)

BASE = {
    "NIRNA.md": "# Project Rules\nUse /review when needed.\n",
    ".iron-laws.yml": "forbid_env_drop: true\n",
    ".nerdvana/skills/review/SKILL.md": "---\nname: review\ndescription: code review\n---\nBody",
}


def test_omitted_is_inherited_and_invariant_guardrail_protection() -> None:
    base  = dict(BASE)
    delta = {
        ".nerdvana/skills/audit/SKILL.md": "---\nname: audit\ndescription: security audit\n---\nRun python3 scripts/scan.py",
        "scripts/scan.py": "print('ok')\n",
    }

    merged = apply_delta_tree(NERDVANA_SURFACE, base, delta)

    assert "NIRNA.md" in merged
    assert merged[".iron-laws.yml"] == base[".iron-laws.yml"]
    assert merged["scripts/scan.py"] == "print('ok')\n"

    triggered = autotrigger_skills(NERDVANA_SURFACE, merged)
    assert "audit" in triggered
    assert "audit" in merged["NIRNA.md"]
    assert validate_harness_tree(NERDVANA_SURFACE, base, merged) == []


def test_rejects_invariant_guardrail_modification_and_missing_script() -> None:
    base = {
        "NIRNA.md": "# Rules\n",
        ".iron-laws.yml": "forbid_env_drop: true\n",
    }
    bad_candidate = {
        "NIRNA.md": "# Rules\nUse audit skill.\n",
        ".iron-laws.yml": "forbid_env_drop: false\n",
        ".nerdvana/skills/audit/SKILL.md": "---\nname: audit\ndescription: sec\n---\npython3 scripts/missing.py",
    }

    errors = validate_harness_tree(NERDVANA_SURFACE, base, bad_candidate)

    assert any("Immutable guardrail" in e for e in errors)
    assert any("missing.py" in e for e in errors)


def test_a_path_that_escapes_the_surface_is_refused() -> None:
    with pytest.raises(ValueError):
        normalize_rel("../outside.md")
    with pytest.raises(ValueError):
        normalize_rel("/etc/passwd")


def test_a_path_off_the_surface_is_not_editable() -> None:
    assert is_editable_path(NERDVANA_SURFACE, "NIRNA.md") is True
    assert is_editable_path(NERDVANA_SURFACE, ".nerdvana/skills/audit/SKILL.md") is True
    assert is_editable_path(NERDVANA_SURFACE, ".nerdvana/skills/audit.md") is True
    assert is_editable_path(NERDVANA_SURFACE, ".nerdvana/agents/reviewer.yml") is True
    assert is_editable_path(NERDVANA_SURFACE, "scripts/scan.py") is True
    assert is_editable_path(NERDVANA_SURFACE, ".iron-laws.yml") is False
    assert is_editable_path(NERDVANA_SURFACE, "src/main.py") is False


def test_a_delta_off_the_surface_is_refused() -> None:
    with pytest.raises(ValueError, match="not on editable surface"):
        apply_delta_tree(NERDVANA_SURFACE, dict(BASE), {"src/main.py": "print('x')\n"})


def test_an_empty_always_on_file_is_refused() -> None:
    base      = {"NIRNA.md": "# Rules\n", ".iron-laws.yml": "forbid_env_drop: true\n"}
    candidate = {"NIRNA.md": "   ", ".iron-laws.yml": "forbid_env_drop: true\n"}

    errors = validate_harness_tree(NERDVANA_SURFACE, base, candidate)

    assert any("must never be empty" in e for e in errors)


def test_an_always_on_file_over_the_budget_is_refused() -> None:
    base = {"NIRNA.md": "# Rules\n", "CLAUDE.md": "# Rules\n", ".iron-laws.yml": "x\n"}
    big  = "x" * NERDVANA_SURFACE.always_on_max_chars
    candidate = {"NIRNA.md": big, "CLAUDE.md": big, ".iron-laws.yml": "x\n"}

    errors = validate_harness_tree(NERDVANA_SURFACE, base, candidate)

    assert any("Always-on context budget exceeded" in e for e in errors)


def test_the_skill_names_of_the_tree() -> None:
    tree = {
        ".nerdvana/skills/review/SKILL.md": "x",
        ".nerdvana/skills/audit.md": "x",
        "NIRNA.md": "x",
    }

    assert skill_names(tree) == ["audit", "review"]


def test_a_skill_that_is_already_named_is_not_added_again() -> None:
    tree = {
        "NIRNA.md": "# Rules\nUse the audit skill when needed.\n",
        ".nerdvana/skills/audit/SKILL.md": "x",
    }

    added = autotrigger_skills(NERDVANA_SURFACE, tree)

    assert added == []
    assert "Auto-Triggered Skills" not in tree["NIRNA.md"]
