"""Tests for onboarding tools — Phase E.

Author: 최진호
Date:   2026-04-18
"""

from __future__ import annotations

from pathlib import Path

import pytest

from nerdvana_cli.core.memories import MemoriesManager
from nerdvana_cli.core.tool import ToolContext
from nerdvana_cli.tools.memory_tools import (
    CheckOnboardingPerformedTool,
    OnboardingTool,
    InitialInstructionsTool,
)


def _ctx(tmp_path: Path) -> ToolContext:
    project = tmp_path / "onboard_proj"
    project.mkdir(exist_ok=True)
    return ToolContext(cwd=str(project))


@pytest.mark.asyncio
async def test_check_before_onboarding(tmp_path: Path) -> None:
    tool   = CheckOnboardingPerformedTool()
    ctx    = _ctx(tmp_path)
    result = await tool.call(None, ctx, can_use_tool=None)
    assert not result.is_error
    assert "not yet" in result.content


@pytest.mark.asyncio
async def test_onboarding_sets_stamp(tmp_path: Path) -> None:
    tool = OnboardingTool()
    ctx  = _ctx(tmp_path)
    result = await tool.call(None, ctx, can_use_tool=None)
    assert not result.is_error
    mgr = MemoriesManager(ctx.cwd)
    assert mgr.onboarding_exists()


@pytest.mark.asyncio
async def test_check_after_onboarding(tmp_path: Path) -> None:
    onboard_tool = OnboardingTool()
    check_tool   = CheckOnboardingPerformedTool()
    ctx          = _ctx(tmp_path)
    await onboard_tool.call(None, ctx, can_use_tool=None)
    result = await check_tool.call(None, ctx, can_use_tool=None)
    assert "completed" in result.content


@pytest.mark.asyncio
async def test_initial_instructions_returns_sections(tmp_path: Path) -> None:
    tool = InitialInstructionsTool()
    ctx  = _ctx(tmp_path)
    result = await tool.call(None, ctx, can_use_tool=None)
    assert not result.is_error
    assert "NIRNA.md" in result.content
    assert "Project Memories" in result.content


@pytest.mark.asyncio
async def test_initial_instructions_lists_written_memories(tmp_path: Path) -> None:
    from nerdvana_cli.tools.memory_tools import WriteMemoryTool
    write_tool = WriteMemoryTool()
    init_tool  = InitialInstructionsTool()
    ctx        = _ctx(tmp_path)
    parsed     = write_tool.parse_args({"name": "build", "content": "pytest", "scope": "project_knowledge"})
    await write_tool.call(parsed, ctx, can_use_tool=None)
    result = await init_tool.call(None, ctx, can_use_tool=None)
    assert "build" in result.content
