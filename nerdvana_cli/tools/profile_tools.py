"""Profile management tools — GetCurrentConfig, ActivateMode, DeactivateMode.

작성자: 최진호
작성일: 2026-04-18
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from nerdvana_cli.core.profiles import ProfileManager
from nerdvana_cli.core.tool import BaseTool, ToolCategory, ToolContext, ToolSideEffect
from nerdvana_cli.types import PermissionBehavior, PermissionResult, ToolResult

# ---------------------------------------------------------------------------
# GetCurrentConfig
# ---------------------------------------------------------------------------

class GetCurrentConfigTool(BaseTool[None]):
    """Return a JSON summary of the currently active context + mode profiles."""

    name             = "GetCurrentConfig"
    description_text = (
        "Return a summary of the currently active runtime profiles (context and mode). "
        "Includes trust level, excluded/included tools, model override, and prompt fragments."
    )
    input_schema: dict[str, Any] = {"type": "object", "properties": {}, "required": []}
    category         = ToolCategory.READ
    side_effects     = ToolSideEffect.NONE
    is_concurrency_safe = True

    def __init__(self, profile_manager: ProfileManager) -> None:
        self._pm = profile_manager

    async def call(
        self,
        args: None,
        context: ToolContext,
        can_use_tool: Any,
        on_progress: Any = None,
    ) -> ToolResult:
        summary = self._pm.current_config_summary()
        return ToolResult(tool_use_id="", content=json.dumps(summary, indent=2, ensure_ascii=False))


# ---------------------------------------------------------------------------
# ActivateMode
# ---------------------------------------------------------------------------

@dataclass
class ActivateModeArgs:
    name: str


class ActivateModeTool(BaseTool[ActivateModeArgs]):
    """Push a named mode onto the active mode stack."""

    name             = "ActivateMode"
    description_text = (
        "Activate a named mode profile (e.g. 'planning', 'editing', 'one-shot'). "
        "The mode is pushed onto a stack; use DeactivateMode to revert."
    )
    input_schema: dict[str, Any] = {
        "type":       "object",
        "properties": {
            "name": {"type": "string", "description": "Mode profile name to activate"},
        },
        "required":   ["name"],
    }
    args_class   = ActivateModeArgs
    category     = ToolCategory.META
    side_effects = ToolSideEffect.NONE

    def __init__(self, profile_manager: ProfileManager) -> None:
        self._pm = profile_manager

    async def call(
        self,
        args: ActivateModeArgs,
        context: ToolContext,
        can_use_tool: Any,
        on_progress: Any = None,
    ) -> ToolResult:
        try:
            profile = self._pm.push_mode(args.name)
            return ToolResult(
                tool_use_id = "",
                content     = f"Mode '{profile.name}' activated (trust={profile.trust_level}). "
                              f"Stack: {self._pm.mode_stack}",
            )
        except ValueError as exc:
            return ToolResult(tool_use_id="", content=f"Error: {exc}", is_error=True)

    def check_permissions(self, args: ActivateModeArgs, context: ToolContext) -> PermissionResult:
        return PermissionResult(behavior=PermissionBehavior.ALLOW)


# ---------------------------------------------------------------------------
# DeactivateMode
# ---------------------------------------------------------------------------

@dataclass
class DeactivateModeArgs:
    name: str | None = None


class DeactivateModeTool(BaseTool[DeactivateModeArgs]):
    """Pop the top mode from the stack (or a specific named mode)."""

    name             = "DeactivateMode"
    description_text = (
        "Deactivate the top-of-stack mode profile. "
        "Optionally specify a name to confirm which mode to remove."
    )
    input_schema: dict[str, Any] = {
        "type":       "object",
        "properties": {
            "name": {
                "type":        ["string", "null"],
                "description": "Optional: name of the mode to deactivate (must match top of stack)",
            },
        },
        "required": [],
    }
    args_class   = DeactivateModeArgs
    category     = ToolCategory.META
    side_effects = ToolSideEffect.NONE

    def __init__(self, profile_manager: ProfileManager) -> None:
        self._pm = profile_manager

    async def call(
        self,
        args: DeactivateModeArgs,
        context: ToolContext,
        can_use_tool: Any,
        on_progress: Any = None,
    ) -> ToolResult:
        current_top = self._pm.active_mode_name
        if args.name and args.name != current_top:
            return ToolResult(
                tool_use_id = "",
                content     = f"Error: top of stack is '{current_top}', not '{args.name}'",
                is_error    = True,
            )
        removed = self._pm.pop_mode()
        if removed:
            return ToolResult(
                tool_use_id = "",
                content     = f"Mode '{removed}' deactivated. Current: {self._pm.active_mode_name}",
            )
        return ToolResult(tool_use_id="", content="No mode to deactivate — stack already at default.")

    def check_permissions(self, args: DeactivateModeArgs, context: ToolContext) -> PermissionResult:
        return PermissionResult(behavior=PermissionBehavior.ALLOW)
