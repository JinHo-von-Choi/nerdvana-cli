"""ActivateSkill: load one skill's full instructions on demand (tier 2 of Agent Skills progressive disclosure)."""

from __future__ import annotations

from dataclasses import dataclass
from html import escape
from typing import Any, ClassVar

from nerdvana_cli.core.skills import MAX_BUNDLED_FILES, Skill, SkillLoader, SkillLoadError
from nerdvana_cli.core.tool import BaseTool, ToolCategory, ToolContext, ToolSideEffect
from nerdvana_cli.types import ToolResult

# Compaction recognises activated skill instructions by this tag and keeps them.
SKILL_CONTENT_TAG = "skill_content"


@dataclass
class ActivateSkillArgs:
    name: str


def format_activation(skill: Skill) -> str:
    """The skill body wrapped in ``<skill_content>``, then where it lives and the files it bundles (listed, not read)."""
    lines = [f'<{SKILL_CONTENT_TAG} name="{escape(skill.name, quote=True)}">', skill.body]
    directory = skill.directory
    if directory is not None:
        lines += ["", f"Skill directory: {directory}", "Relative paths in this skill are relative to the skill directory."]
    if skill.compatibility:
        lines.append(f"Compatibility: {skill.compatibility}")
    if skill.allowed_tools:
        lines.append(
            "Allowed tools declared by this skill (informational; every call still goes through the normal "
            f"permission system): {' '.join(skill.allowed_tools)}"
        )
    files, truncated = skill.bundled_files()
    if files:
        lines += ["", "<skill_resources>", *(f"  <file>{escape(f)}</file>" for f in files)]
        if truncated:
            lines.append(f"  <!-- listing cut at {MAX_BUNDLED_FILES} files -->")
        lines.append("</skill_resources>")
    lines.append(f"</{SKILL_CONTENT_TAG}>")
    return "\n".join(lines)


class ActivateSkillTool(BaseTool[ActivateSkillArgs]):
    """Return the instructions of a skill from the catalog in the system prompt."""

    name             = "ActivateSkill"
    description_text = (
        "Load the full instructions of one skill listed under 'Skills' in the system prompt. The result holds the "
        "skill's instructions and the files it bundles (scripts, references, assets); read those with the file "
        "tools when the instructions call for them."
    )
    args_class                       = ActivateSkillArgs
    category: ClassVar[ToolCategory] = ToolCategory.META
    side_effects                     = ToolSideEffect.NONE
    is_concurrency_safe              = False

    def __init__(self, loader: SkillLoader) -> None:
        self.loader       = loader
        self.input_schema: dict[str, Any] = {
            "type": "object",
            "properties": {
                "name": {
                    "type":        "string",
                    "enum":        [skill.name for skill in loader.model_skills()],
                    "description": "Name of the skill to load",
                },
            },
            "required": ["name"],
        }

    def catalog_lines(self) -> list[str]:
        """One ``- name: description`` line per skill the model may activate, ordered by name."""
        return [f"- {skill.name}: {' '.join(skill.description.split())}" for skill in self.loader.model_skills()]

    async def call(self, args: ActivateSkillArgs, context: ToolContext, can_use_tool: Any = None, on_progress: Any = None) -> ToolResult:
        skill = self.loader.get_by_name(args.name)
        if skill is None or not skill.model_invocable:
            known = ", ".join(s.name for s in self.loader.model_skills())
            return ToolResult(tool_use_id="", content=f"No skill named '{args.name}'. Available skills: {known}.", is_error=True)
        if self.loader.is_activated(skill.name):
            return ToolResult(
                tool_use_id="",
                content=f"Skill '{skill.name}' is already active in this conversation. Follow the instructions loaded earlier.",
            )
        if skill.remote is not None:
            try:
                skill = await skill.remote(skill, context)
            except SkillLoadError as exc:
                return ToolResult(tool_use_id="", content=f"Skill '{skill.name}' was not loaded: {exc}", is_error=True)
        self.loader.mark_activated(skill.name)
        return ToolResult(tool_use_id="", content=format_activation(skill))
