"""Profile management tools: GetCurrentConfig.

작성자: 최진호
작성일: 2026-04-18
"""

from __future__ import annotations

import json
from typing import Any

from nerdvana_cli.core.profiles import ProfileManager
from nerdvana_cli.core.tool import BaseTool, ToolCategory, ToolContext, ToolSideEffect
from nerdvana_cli.types import ToolResult

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
