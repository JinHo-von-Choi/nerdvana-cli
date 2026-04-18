"""Tests for memory tools (WriteMemory, ReadMemory, etc.) — Phase E.

Covers: schema validation, secret scanner, scope routing,
        all 9 tools' call() behaviour.

Author: 최진호
Date:   2026-04-18
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from nerdvana_cli.core.tool import ToolContext
from nerdvana_cli.tools.memory_tools import (
    CheckOnboardingPerformedTool,
    DeleteMemoryTool,
    EditMemoryTool,
    InitialInstructionsTool,
    ListMemoriesTool,
    OnboardingTool,
    ReadMemoryTool,
    RenameMemoryTool,
    WriteMemoryTool,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ctx(tmp_path: Path) -> ToolContext:
    project = tmp_path / "proj"
    project.mkdir(exist_ok=True)
    return ToolContext(cwd=str(project))


async def _call(tool: object, args: dict, ctx: ToolContext):  # type: ignore[return]
    parsed = tool.parse_args(args)  # type: ignore[union-attr]
    return await tool.call(parsed, ctx, can_use_tool=None)  # type: ignore[union-attr]


# ===========================================================================
# WriteMemory
# ===========================================================================

@pytest.mark.asyncio
async def test_write_memory_success(tmp_path: Path) -> None:
    tool = WriteMemoryTool()
    ctx  = _ctx(tmp_path)
    result = await _call(tool, {"name": "k1", "content": "hello", "scope": "project_knowledge"}, ctx)
    assert not result.is_error
    assert "k1" in result.content


@pytest.mark.asyncio
async def test_write_memory_invalid_scope(tmp_path: Path) -> None:
    tool = WriteMemoryTool()
    ctx  = _ctx(tmp_path)
    result = await _call(tool, {"name": "k1", "content": "hi", "scope": "invalid_scope"}, ctx)
    assert result.is_error
    assert "Invalid scope" in result.content


@pytest.mark.asyncio
async def test_write_memory_secret_openai_key(tmp_path: Path) -> None:
    tool    = WriteMemoryTool()
    ctx     = _ctx(tmp_path)
    content = "sk-" + "A" * 40  # matches OpenAI pattern
    result  = await _call(tool, {"name": "k1", "content": content, "scope": "project_knowledge"}, ctx)
    assert result.is_error
    assert "blocked" in result.content.lower() or "secret" in result.content.lower()


@pytest.mark.asyncio
async def test_write_memory_project_rule(tmp_path: Path) -> None:
    tool = WriteMemoryTool()
    ctx  = _ctx(tmp_path)
    result = await _call(tool, {"name": "must-use-types", "content": "Always add type hints", "scope": "project_rule"}, ctx)
    assert not result.is_error
    # Should have appended to NIRNA.md
    nirnamd = Path(ctx.cwd) / "NIRNA.md"
    assert nirnamd.exists()
    assert "Always add type hints" in nirnamd.read_text()


@pytest.mark.asyncio
async def test_write_memory_agent_experience_stub(tmp_path: Path) -> None:
    tool   = WriteMemoryTool()
    ctx    = _ctx(tmp_path)
    result = await _call(tool, {"name": "err1", "content": "some error", "scope": "agent_experience"}, ctx)
    assert result.is_error
    assert "AnchorMind" in result.content


# ===========================================================================
# ReadMemory
# ===========================================================================

@pytest.mark.asyncio
async def test_read_memory_found(tmp_path: Path) -> None:
    write_tool = WriteMemoryTool()
    read_tool  = ReadMemoryTool()
    ctx        = _ctx(tmp_path)
    await _call(write_tool, {"name": "r1", "content": "hello", "scope": "project_knowledge"}, ctx)
    result = await _call(read_tool, {"name": "r1"}, ctx)
    assert not result.is_error
    assert result.content == "hello"


@pytest.mark.asyncio
async def test_read_memory_not_found(tmp_path: Path) -> None:
    tool   = ReadMemoryTool()
    ctx    = _ctx(tmp_path)
    result = await _call(tool, {"name": "missing"}, ctx)
    assert result.is_error
    assert "not found" in result.content.lower()


# ===========================================================================
# ListMemories
# ===========================================================================

@pytest.mark.asyncio
async def test_list_memories_empty(tmp_path: Path) -> None:
    tool   = ListMemoriesTool()
    ctx    = _ctx(tmp_path)
    result = await _call(tool, {}, ctx)
    assert not result.is_error
    assert "No memories" in result.content


@pytest.mark.asyncio
async def test_list_memories_with_entries(tmp_path: Path) -> None:
    write_tool = WriteMemoryTool()
    list_tool  = ListMemoriesTool()
    ctx        = _ctx(tmp_path)
    await _call(write_tool, {"name": "a", "content": "alpha", "scope": "project_knowledge"}, ctx)
    await _call(write_tool, {"name": "b", "content": "beta",  "scope": "project_knowledge"}, ctx)
    result = await _call(list_tool, {}, ctx)
    assert not result.is_error
    assert "a" in result.content
    assert "b" in result.content


@pytest.mark.asyncio
async def test_list_memories_topic_filter(tmp_path: Path) -> None:
    write_tool = WriteMemoryTool()
    list_tool  = ListMemoriesTool()
    ctx        = _ctx(tmp_path)
    await _call(write_tool, {"name": "auth/jwt", "content": "x", "scope": "project_knowledge"}, ctx)
    await _call(write_tool, {"name": "db/pg",    "content": "y", "scope": "project_knowledge"}, ctx)
    result = await _call(list_tool, {"topic": "auth"}, ctx)
    assert "auth/jwt" in result.content
    assert "db/pg" not in result.content


# ===========================================================================
# DeleteMemory
# ===========================================================================

@pytest.mark.asyncio
async def test_delete_memory_success(tmp_path: Path) -> None:
    write_tool  = WriteMemoryTool()
    delete_tool = DeleteMemoryTool()
    ctx         = _ctx(tmp_path)
    await _call(write_tool,  {"name": "del1", "content": "bye", "scope": "project_knowledge"}, ctx)
    result = await _call(delete_tool, {"name": "del1"}, ctx)
    assert not result.is_error
    assert "Deleted" in result.content


@pytest.mark.asyncio
async def test_delete_memory_not_found(tmp_path: Path) -> None:
    tool   = DeleteMemoryTool()
    ctx    = _ctx(tmp_path)
    result = await _call(tool, {"name": "ghost"}, ctx)
    assert result.is_error


# ===========================================================================
# RenameMemory
# ===========================================================================

@pytest.mark.asyncio
async def test_rename_memory(tmp_path: Path) -> None:
    write_tool  = WriteMemoryTool()
    rename_tool = RenameMemoryTool()
    read_tool   = ReadMemoryTool()
    ctx         = _ctx(tmp_path)
    await _call(write_tool,  {"name": "orig",    "content": "data",  "scope": "project_knowledge"}, ctx)
    await _call(rename_tool, {"old_name": "orig", "new_name": "renamed"}, ctx)
    result = await _call(read_tool, {"name": "renamed"}, ctx)
    assert result.content == "data"


# ===========================================================================
# EditMemory
# ===========================================================================

@pytest.mark.asyncio
async def test_edit_memory_literal(tmp_path: Path) -> None:
    write_tool = WriteMemoryTool()
    edit_tool  = EditMemoryTool()
    read_tool  = ReadMemoryTool()
    ctx        = _ctx(tmp_path)
    await _call(write_tool, {"name": "e1", "content": "Hello World", "scope": "project_knowledge"}, ctx)
    await _call(edit_tool,  {"name": "e1", "needle": "World", "repl": "Python"}, ctx)
    result = await _call(read_tool, {"name": "e1"}, ctx)
    assert result.content == "Hello Python"


@pytest.mark.asyncio
async def test_edit_memory_regex(tmp_path: Path) -> None:
    write_tool = WriteMemoryTool()
    edit_tool  = EditMemoryTool()
    read_tool  = ReadMemoryTool()
    ctx        = _ctx(tmp_path)
    await _call(write_tool, {"name": "e2", "content": "v1 v2", "scope": "project_knowledge"}, ctx)
    await _call(edit_tool,  {"name": "e2", "needle": r"v(\d)", "repl": r"ver-\1", "mode": "regex"}, ctx)
    result = await _call(read_tool, {"name": "e2"}, ctx)
    assert "ver-1" in result.content


# ===========================================================================
# CheckOnboardingPerformed
# ===========================================================================

@pytest.mark.asyncio
async def test_check_onboarding_not_done(tmp_path: Path) -> None:
    tool   = CheckOnboardingPerformedTool()
    ctx    = _ctx(tmp_path)
    result = await tool.call(None, ctx, can_use_tool=None)
    assert "not yet" in result.content


@pytest.mark.asyncio
async def test_check_onboarding_done(tmp_path: Path) -> None:
    from nerdvana_cli.core.memories import MemoriesManager
    tool = CheckOnboardingPerformedTool()
    ctx  = _ctx(tmp_path)
    MemoriesManager(ctx.cwd).mark_onboarding_done()
    result = await tool.call(None, ctx, can_use_tool=None)
    assert "completed" in result.content


# ===========================================================================
# Onboarding
# ===========================================================================

@pytest.mark.asyncio
async def test_onboarding_creates_stamp(tmp_path: Path) -> None:
    from nerdvana_cli.core import paths as core_paths
    tool   = OnboardingTool()
    ctx    = _ctx(tmp_path)
    result = await tool.call(None, ctx, can_use_tool=None)
    assert not result.is_error
    assert "onboarding" in result.content.lower()
    assert core_paths.project_onboarding_dir(ctx.cwd).exists()


# ===========================================================================
# InitialInstructions
# ===========================================================================

@pytest.mark.asyncio
async def test_initial_instructions_no_nirnamd(tmp_path: Path) -> None:
    tool   = InitialInstructionsTool()
    ctx    = _ctx(tmp_path)
    result = await tool.call(None, ctx, can_use_tool=None)
    assert not result.is_error
    assert "NIRNA.md" in result.content


@pytest.mark.asyncio
async def test_initial_instructions_with_nirnamd(tmp_path: Path) -> None:
    tool = InitialInstructionsTool()
    ctx  = _ctx(tmp_path)
    nirnamd = Path(ctx.cwd) / "NIRNA.md"
    nirnamd.write_text("# Test Project\n\nBuild: pytest tests/")
    result = await tool.call(None, ctx, can_use_tool=None)
    assert not result.is_error
    assert "pytest" in result.content
