"""MCP tools connected after the registry was built are added to it, with the skills a server offers."""

from __future__ import annotations

from typing import Any

from nerdvana_cli.core.context.skills import Skill, SkillLoader
from nerdvana_cli.core.tool import BaseTool, ToolRegistry
from nerdvana_cli.mcp.skills import McpSkillFileTool
from nerdvana_cli.tools.registry import attach_mcp_tools
from nerdvana_cli.types import ToolResult


class _Remote(BaseTool[Any]):
    name             = "mcp__srv__lookup"
    description_text = "look something up"

    async def call(self, args: Any, context: Any, can_use_tool: Any = None, on_progress: Any = None) -> ToolResult:
        return ToolResult(tool_use_id="", content="ok")


class _Library:
    def skills(self) -> list[Skill]:
        return [Skill(name="remote-skill", description="from the server", trigger="/remote-skill", body="")]


def _skill_file_tool() -> McpSkillFileTool:
    tool = McpSkillFileTool.__new__(McpSkillFileTool)
    tool.library = _Library()  # type: ignore[assignment]
    return tool


def _empty_loader(tmp_path: Any) -> SkillLoader:
    return SkillLoader(project_dir=tmp_path / "p", global_dir=tmp_path / "g")


def test_tools_are_registered(tmp_path: Any) -> None:
    registry = ToolRegistry()
    attach_mcp_tools(registry, [_Remote()], _empty_loader(tmp_path))
    assert registry.get("mcp__srv__lookup") is not None


def test_server_skills_reach_the_catalog_and_activate_skill_is_added(tmp_path: Any) -> None:
    registry = ToolRegistry()
    loader   = _empty_loader(tmp_path)
    attach_mcp_tools(registry, [_skill_file_tool()], loader)
    assert loader.get_by_name("remote-skill") is not None
    assert registry.get("ActivateSkill") is not None
