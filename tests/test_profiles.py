"""Tests for ProfileManager — YAML loading, synthesis, and tool visibility filter.

작성자: 최진호
작성일: 2026-04-18
"""

from __future__ import annotations

import textwrap
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml

from nerdvana_cli.core.profiles import (
    ContextProfile,
    ModeProfile,
    ProfileManager,
)
from nerdvana_cli.core.tool import BaseTool, ToolCategory, ToolRegistry


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def tmp_cwd(tmp_path: Path) -> Path:
    """A temporary cwd with .nerdvana/contexts and .nerdvana/modes directories."""
    (tmp_path / ".nerdvana" / "contexts").mkdir(parents=True)
    (tmp_path / ".nerdvana" / "modes").mkdir(parents=True)
    return tmp_path


def _write_context(tmp_cwd: Path, name: str, data: dict) -> None:
    p = tmp_cwd / ".nerdvana" / "contexts" / f"{name}.yml"
    p.write_text(yaml.dump(data), encoding="utf-8")


def _write_mode(tmp_cwd: Path, name: str, data: dict) -> None:
    p = tmp_cwd / ".nerdvana" / "modes" / f"{name}.yml"
    p.write_text(yaml.dump(data), encoding="utf-8")


def _make_tool(name: str, category: ToolCategory = ToolCategory.READ) -> BaseTool:
    tool = MagicMock(spec=BaseTool)
    tool.name     = name
    tool.category = category
    return tool


def _make_registry(*names: str) -> ToolRegistry:
    reg = ToolRegistry()
    for n in names:
        reg.register(_make_tool(n))
    return reg


# ---------------------------------------------------------------------------
# ContextProfile — from_dict
# ---------------------------------------------------------------------------

class TestContextProfileFromDict:
    def test_minimal_dict(self) -> None:
        p = ContextProfile.from_dict("test", {})
        assert p.name == "test"
        assert p.excluded_tools == []
        assert p.included_tools == []
        assert p.single_project is False

    def test_full_dict(self) -> None:
        p = ContextProfile.from_dict("ide", {
            "description":                "IDE context",
            "prompt_override":            "override",
            "prompt_append":              "append",
            "excluded_tools":             ["Bash", "FileWrite"],
            "included_tools":             ["FileRead"],
            "tool_description_overrides": {"Bash": "new desc"},
            "single_project":             True,
        })
        assert p.description == "IDE context"
        assert p.prompt_override == "override"
        assert p.prompt_append == "append"
        assert p.excluded_tools == ["Bash", "FileWrite"]
        assert p.included_tools == ["FileRead"]
        assert p.tool_description_overrides == {"Bash": "new desc"}
        assert p.single_project is True

    def test_null_prompt_fields(self) -> None:
        p = ContextProfile.from_dict("x", {"prompt_override": None, "prompt_append": None})
        assert p.prompt_override is None
        assert p.prompt_append is None


# ---------------------------------------------------------------------------
# ModeProfile — from_dict
# ---------------------------------------------------------------------------

class TestModeProfileFromDict:
    def test_trust_level_defaults_to_balanced(self) -> None:
        m = ModeProfile.from_dict("x", {})
        assert m.trust_level == "balanced"

    def test_valid_trust_levels(self) -> None:
        for lvl in ("strict", "balanced", "yolo"):
            m = ModeProfile.from_dict("x", {"trust_level": lvl})
            assert m.trust_level == lvl

    def test_invalid_trust_level_fallback(self) -> None:
        m = ModeProfile.from_dict("x", {"trust_level": "aggressive"})
        assert m.trust_level == "balanced"

    def test_model_override_none_by_default(self) -> None:
        m = ModeProfile.from_dict("x", {})
        assert m.model_override is None

    def test_model_override_set(self) -> None:
        m = ModeProfile.from_dict("arch", {"model_override": "claude-opus-4-7"})
        assert m.model_override == "claude-opus-4-7"


# ---------------------------------------------------------------------------
# ProfileManager — built-in profiles
# ---------------------------------------------------------------------------

class TestBuiltinProfiles:
    def test_load_builtin_standalone(self) -> None:
        pm = ProfileManager()
        ctx = pm.load_context("standalone")
        assert ctx.name == "standalone"
        assert ctx.excluded_tools == []

    def test_load_builtin_claude_code(self) -> None:
        pm = ProfileManager()
        ctx = pm.load_context("claude-code")
        assert "Bash" in ctx.excluded_tools
        assert "FileRead" in ctx.excluded_tools

    def test_load_builtin_planning_mode(self) -> None:
        pm = ProfileManager()
        mode = pm.load_mode("planning")
        assert mode.trust_level == "strict"
        assert "Bash" in mode.excluded_tools

    def test_load_builtin_yolo_mode(self) -> None:
        pm = ProfileManager()
        mode = pm.load_mode("one-shot")
        assert mode.trust_level == "yolo"

    def test_load_builtin_architect_mode(self) -> None:
        pm = ProfileManager()
        mode = pm.load_mode("architect")
        assert mode.trust_level == "strict"
        assert mode.model_override is not None

    def test_unknown_profile_raises(self) -> None:
        pm = ProfileManager()
        with pytest.raises(ValueError, match="not found"):
            pm.load_context("nonexistent-context-xyz")


# ---------------------------------------------------------------------------
# ProfileManager — project-local override
# ---------------------------------------------------------------------------

class TestProjectLocalOverride:
    def test_project_context_overrides_builtin(self, tmp_cwd: Path) -> None:
        _write_context(tmp_cwd, "standalone", {
            "description":   "custom standalone",
            "excluded_tools": ["MyTool"],
        })
        pm  = ProfileManager(cwd=str(tmp_cwd))
        ctx = pm.load_context("standalone")
        assert ctx.excluded_tools == ["MyTool"]
        assert ctx.description == "custom standalone"

    def test_project_mode_overrides_builtin(self, tmp_cwd: Path) -> None:
        _write_mode(tmp_cwd, "planning", {
            "trust_level": "yolo",
        })
        pm   = ProfileManager(cwd=str(tmp_cwd))
        mode = pm.load_mode("planning")
        assert mode.trust_level == "yolo"


# ---------------------------------------------------------------------------
# ProfileManager — synthesis
# ---------------------------------------------------------------------------

class TestMergedProfile:
    def test_merged_defaults(self) -> None:
        pm = ProfileManager()
        m  = pm.merged()
        assert m.context_name == "standalone"
        assert m.mode_name    == "interactive"
        assert m.trust_level  == "balanced"

    def test_mode_override_wins_for_prompt(self, tmp_cwd: Path) -> None:
        _write_context(tmp_cwd, "standalone", {"prompt_override": "ctx override"})
        _write_mode(tmp_cwd, "interactive", {"prompt_override": "mode override"})
        pm = ProfileManager(cwd=str(tmp_cwd))
        m  = pm.merged()
        assert m.prompt_override == "mode override"

    def test_context_override_used_when_mode_has_none(self, tmp_cwd: Path) -> None:
        _write_context(tmp_cwd, "standalone", {"prompt_override": "ctx override"})
        _write_mode(tmp_cwd, "interactive", {})
        pm = ProfileManager(cwd=str(tmp_cwd))
        m  = pm.merged()
        assert m.prompt_override == "ctx override"

    def test_prompt_append_concatenated(self, tmp_cwd: Path) -> None:
        _write_context(tmp_cwd, "standalone", {"prompt_append": "ctx-append"})
        _write_mode(tmp_cwd, "interactive", {"prompt_append": "mode-append"})
        pm = ProfileManager(cwd=str(tmp_cwd))
        m  = pm.merged()
        assert "ctx-append" in m.prompt_append
        assert "mode-append" in m.prompt_append

    def test_excluded_tools_union(self, tmp_cwd: Path) -> None:
        _write_context(tmp_cwd, "standalone", {"excluded_tools": ["ToolA"]})
        _write_mode(tmp_cwd, "interactive", {"excluded_tools": ["ToolB"]})
        pm = ProfileManager(cwd=str(tmp_cwd))
        m  = pm.merged()
        assert "ToolA" in m.excluded_tools
        assert "ToolB" in m.excluded_tools

    def test_model_override_from_mode(self, tmp_cwd: Path) -> None:
        _write_mode(tmp_cwd, "interactive", {"model_override": "claude-opus-4-7"})
        pm = ProfileManager(cwd=str(tmp_cwd))
        m  = pm.merged()
        assert m.model_override == "claude-opus-4-7"

    def test_description_overrides_merged(self, tmp_cwd: Path) -> None:
        _write_context(tmp_cwd, "standalone", {"tool_description_overrides": {"Bash": "ctx-desc"}})
        _write_mode(tmp_cwd, "interactive", {"tool_description_overrides": {"Bash": "mode-desc"}})
        pm = ProfileManager(cwd=str(tmp_cwd))
        m  = pm.merged()
        assert m.tool_description_overrides["Bash"] == "mode-desc"


# ---------------------------------------------------------------------------
# ProfileManager — visible_tools
# ---------------------------------------------------------------------------

class TestVisibleTools:
    def test_no_filter_returns_all(self) -> None:
        pm  = ProfileManager()
        reg = _make_registry("Bash", "FileRead", "FileWrite")
        assert len(pm.visible_tools(reg)) == 3

    def test_excluded_tools_removed(self, tmp_cwd: Path) -> None:
        _write_context(tmp_cwd, "standalone", {"excluded_tools": ["Bash"]})
        pm  = ProfileManager(cwd=str(tmp_cwd))
        reg = _make_registry("Bash", "FileRead", "FileWrite")
        names = [t.name for t in pm.visible_tools(reg)]
        assert "Bash" not in names
        assert "FileRead" in names

    def test_included_tools_restricts(self, tmp_cwd: Path) -> None:
        _write_mode(tmp_cwd, "interactive", {"included_tools": ["FileRead"]})
        pm  = ProfileManager(cwd=str(tmp_cwd))
        reg = _make_registry("Bash", "FileRead", "FileWrite")
        names = [t.name for t in pm.visible_tools(reg)]
        assert names == ["FileRead"]

    def test_included_minus_excluded(self, tmp_cwd: Path) -> None:
        _write_mode(tmp_cwd, "interactive", {
            "included_tools": ["FileRead", "Bash"],
            "excluded_tools": ["Bash"],
        })
        pm  = ProfileManager(cwd=str(tmp_cwd))
        reg = _make_registry("Bash", "FileRead", "FileWrite")
        names = [t.name for t in pm.visible_tools(reg)]
        assert "Bash" not in names
        assert "FileRead" in names
        assert "FileWrite" not in names


# ---------------------------------------------------------------------------
# ProfileManager — mode stack operations
# ---------------------------------------------------------------------------

class TestModeStack:
    def test_push_and_pop(self) -> None:
        pm = ProfileManager()
        pm.push_mode("planning")
        assert pm.active_mode_name == "planning"
        removed = pm.pop_mode()
        assert removed == "planning"
        assert pm.active_mode_name == "interactive"

    def test_pop_at_default_returns_none(self) -> None:
        pm      = ProfileManager()
        removed = pm.pop_mode()
        assert removed is None
        assert pm.active_mode_name == "interactive"

    def test_set_mode_replaces_stack(self) -> None:
        pm = ProfileManager()
        pm.push_mode("planning")
        pm.push_mode("query")
        pm.set_mode("editing")
        assert pm.mode_stack == ["editing"]

    def test_set_context(self) -> None:
        pm = ProfileManager()
        pm.set_context("claude-code")
        assert pm.active_context_name == "claude-code"


# ---------------------------------------------------------------------------
# ProfileManager — available names discovery
# ---------------------------------------------------------------------------

class TestDiscovery:
    def test_builtin_contexts_discovered(self) -> None:
        pm    = ProfileManager()
        names = pm.available_contexts()
        assert "standalone" in names
        assert "claude-code" in names
        assert "ide" in names

    def test_builtin_modes_discovered(self) -> None:
        pm    = ProfileManager()
        names = pm.available_modes()
        assert "planning" in names
        assert "editing" in names
        assert "one-shot" in names
        assert "architect" in names

    def test_project_custom_context_discovered(self, tmp_cwd: Path) -> None:
        _write_context(tmp_cwd, "my-custom-ctx", {})
        pm    = ProfileManager(cwd=str(tmp_cwd))
        names = pm.available_contexts()
        assert "my-custom-ctx" in names
