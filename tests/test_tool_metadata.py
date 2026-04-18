"""Tests for BaseTool metadata fields and ToolRegistry.filter() API."""

from __future__ import annotations

import pytest

from nerdvana_cli.core.tool import ToolCategory, ToolRegistry, ToolSideEffect
from nerdvana_cli.tools.bash_tool import BashTool, create_bash_tool
from nerdvana_cli.tools.file_tools import FileEditTool, FileReadTool, FileWriteTool
from nerdvana_cli.tools.search_tools import GlobTool, GrepTool


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_registry() -> ToolRegistry:
    """Create a minimal registry with all standard non-agent tools."""
    from nerdvana_cli.tools.parism_tool import ParismTool

    registry = ToolRegistry()
    registry.register(create_bash_tool())
    registry.register(FileReadTool())
    registry.register(FileWriteTool())
    registry.register(FileEditTool())
    registry.register(GlobTool())
    registry.register(GrepTool())
    # ParismTool has metadata but requires a client; register without setting one
    registry.register(ParismTool())
    return registry


def _make_meta_registry() -> ToolRegistry:
    """Extend the standard registry with team/task tools."""
    from nerdvana_cli.core.task_state import TaskRegistry
    from nerdvana_cli.core.team import TeamRegistry
    from nerdvana_cli.tools.team_tools import (
        SendMessageTool,
        TaskGetTool,
        TaskStopTool,
        TeamCreateTool,
    )

    registry = _make_registry()
    _task_reg = TaskRegistry()
    _team_reg = TeamRegistry()
    registry.register(TeamCreateTool(team_registry=_team_reg))
    registry.register(SendMessageTool(team_registry=_team_reg))
    registry.register(TaskGetTool(task_registry=_task_reg))
    registry.register(TaskStopTool(task_registry=_task_reg))
    return registry


def _make_lsp_registry() -> ToolRegistry:
    """Registry with mock LSP tools (no real LSP server needed)."""
    from unittest.mock import MagicMock

    from nerdvana_cli.tools.lsp import (
        LspDiagnosticsTool,
        LspFindReferencesTool,
        LspGotoDefinitionTool,
        LspRenameTool,
    )

    mock_client = MagicMock()
    registry    = ToolRegistry()
    registry.register(LspDiagnosticsTool(client=mock_client))
    registry.register(LspGotoDefinitionTool(client=mock_client))
    registry.register(LspFindReferencesTool(client=mock_client))
    registry.register(LspRenameTool(client=mock_client))
    return registry


# ---------------------------------------------------------------------------
# Test 1: every registered tool has category and side_effects defined
# ---------------------------------------------------------------------------


def test_all_tools_have_category_and_side_effects() -> None:
    """All built-in tools must expose category and side_effects ClassVars."""
    registry = _make_meta_registry()
    for tool in registry.all_tools():
        assert hasattr(tool, "category"),     f"{tool.name} missing .category"
        assert hasattr(tool, "side_effects"), f"{tool.name} missing .side_effects"
        assert isinstance(tool.category,    ToolCategory),    f"{tool.name}.category wrong type"
        assert isinstance(tool.side_effects, ToolSideEffect), f"{tool.name}.side_effects wrong type"


# ---------------------------------------------------------------------------
# Test 2: filter(category=READ) returns all READ tools
# ---------------------------------------------------------------------------


def test_filter_by_read_category_returns_all_read_tools() -> None:
    """filter(category=READ) must include FileRead, Glob, Grep."""
    registry   = _make_registry()
    read_tools = registry.filter(category=ToolCategory.READ)
    names      = {t.name for t in read_tools}
    assert "FileRead" in names
    assert "Glob"     in names
    assert "Grep"     in names
    # WRITE tools must not appear
    assert "Bash"      not in names
    assert "FileWrite" not in names
    assert "FileEdit"  not in names


# ---------------------------------------------------------------------------
# Test 3: filter(read_only=True) matches is_read_only property
# ---------------------------------------------------------------------------


def test_filter_read_only_matches_is_read_only_property() -> None:
    """filter(read_only=True) result must equal legacy is_read_only=True set."""
    registry = _make_registry()
    via_filter  = {t.name for t in registry.filter(read_only=True)}
    via_prop    = {t.name for t in registry.all_tools() if t.is_read_only}
    assert via_filter == via_prop, (
        f"Mismatch: filter={via_filter}, property={via_prop}"
    )


# ---------------------------------------------------------------------------
# Test 4: filter(tags_all={"lsp","symbol"}) returns the three symbol-aware
# LSP tools (goto_definition, find_references, and rename).
# ---------------------------------------------------------------------------


def test_filter_tags_all_lsp_symbol_returns_symbol_tools() -> None:
    """lsp_goto_definition, lsp_find_references, and lsp_rename carry lsp+symbol tags.

    lsp_rename gained the ``symbol`` tag in the T-debt-lsp-rename-symbol-tag
    cleanup: it is a symbol-level refactoring tool and should surface in the
    same filter as its read-only siblings. Phase F profiles still distinguish
    read vs write via ``category``/``requires_confirmation``, so this does not
    weaken any safety gate.
    """
    registry = _make_lsp_registry()
    result   = registry.filter(tags_all={"lsp", "symbol"})
    names    = {t.name for t in result}
    assert names == {"lsp_goto_definition", "lsp_find_references", "lsp_rename"}, (
        f"Unexpected result: {names}"
    )


# ---------------------------------------------------------------------------
# Test 5: filter(requires_confirmation=True) includes TaskStop and lsp_rename
# ---------------------------------------------------------------------------


def test_filter_requires_confirmation_includes_taskstop_and_lsp_rename() -> None:
    """TaskStop and lsp_rename both set requires_confirmation=True."""
    from nerdvana_cli.core.task_state import TaskRegistry
    from nerdvana_cli.core.team import TeamRegistry
    from unittest.mock import MagicMock

    from nerdvana_cli.tools.lsp import LspRenameTool
    from nerdvana_cli.tools.team_tools import TaskStopTool

    registry = ToolRegistry()
    registry.register(TaskStopTool(task_registry=TaskRegistry()))
    registry.register(LspRenameTool(client=MagicMock()))

    result = registry.filter(requires_confirmation=True)
    names  = {t.name for t in result}
    assert "TaskStop"   in names
    assert "lsp_rename" in names


# ---------------------------------------------------------------------------
# Test 6: filter(tags_any) uses OR semantics
# ---------------------------------------------------------------------------


def test_filter_tags_any_uses_or_semantics() -> None:
    """filter(tags_any={"shell","search"}) returns Bash, Parism, Glob, Grep."""
    registry = _make_registry()
    result   = registry.filter(tags_any={"shell", "search"})
    names    = {t.name for t in result}
    assert "Bash"   in names
    assert "Parism" in names
    assert "Glob"   in names
    assert "Grep"   in names
    assert "FileRead"  not in names
    assert "FileWrite" not in names


# ---------------------------------------------------------------------------
# Test 7: filter with no args returns all tools
# ---------------------------------------------------------------------------


def test_filter_no_args_returns_all_tools() -> None:
    """filter() with no arguments must return the full tool list."""
    registry = _make_meta_registry()
    assert set(t.name for t in registry.filter()) == set(
        t.name for t in registry.all_tools()
    )


# ---------------------------------------------------------------------------
# Test 8: is_read_only property backward compatibility
# ---------------------------------------------------------------------------


def test_is_read_only_backward_compat() -> None:
    """is_read_only must be True for READ/SYMBOLIC, False for WRITE/META."""
    assert FileReadTool().is_read_only is True
    assert GlobTool().is_read_only     is True
    assert GrepTool().is_read_only     is True
    assert BashTool().is_read_only     is False
    assert FileWriteTool().is_read_only is False
    assert FileEditTool().is_read_only  is False


# ---------------------------------------------------------------------------
# Test 9: META category tools are not read-only
# ---------------------------------------------------------------------------


def test_meta_tools_are_not_read_only() -> None:
    """All META category tools must have is_read_only == False."""
    registry = _make_meta_registry()
    meta_tools = registry.filter(category=ToolCategory.META)
    assert len(meta_tools) >= 4, "Expected at least 4 META tools"
    for tool in meta_tools:
        assert tool.is_read_only is False, f"{tool.name} is META but is_read_only=True"


# ---------------------------------------------------------------------------
# Test 10: filter with set of categories (multi-category)
# ---------------------------------------------------------------------------


def test_filter_set_of_categories() -> None:
    """filter(category={READ, META}) must include both READ and META tools."""
    registry = _make_meta_registry()
    result   = registry.filter(category={ToolCategory.READ, ToolCategory.META})
    cats     = {t.category for t in result}
    assert ToolCategory.READ in cats
    assert ToolCategory.META in cats
    assert ToolCategory.WRITE not in cats
