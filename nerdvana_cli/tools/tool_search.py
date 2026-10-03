"""ToolSearch: load deferred tools so the model can call them."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, ClassVar

from nerdvana_cli.core.tool import BaseTool, ToolCategory, ToolContext, ToolSideEffect
from nerdvana_cli.core.tool_index import ToolIndex
from nerdvana_cli.types import ToolResult


@dataclass
class ToolSearchArgs:
    query: str


class ToolSearchTool(BaseTool[ToolSearchArgs]):
    """Find tools that are listed by name only, and load them."""

    name             = "ToolSearch"
    description_text = (
        "Load tools that the system prompt lists under 'Deferred tools'. Pass 'select:name1,name2' to load "
        "those tools, or keywords to search their names and descriptions. A loaded tool can be called from "
        "your next step on; this result shows its description and parameters."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {"query": {"type": "string", "description": "'select:name1,name2' or search keywords"}},
        "required": ["query"],
    }
    args_class                       = ToolSearchArgs
    category: ClassVar[ToolCategory] = ToolCategory.META
    side_effects                     = ToolSideEffect.NONE
    is_concurrency_safe              = False

    def __init__(self, index: ToolIndex) -> None:
        self._index = index

    async def call(self, args: ToolSearchArgs, context: ToolContext, can_use_tool: Any = None, on_progress: Any = None) -> ToolResult:
        matches = self._index.search(args.query)
        if not matches:
            return ToolResult(tool_use_id="", content=f"No deferred tool matches '{args.query}'. Names are listed in the system prompt.", is_error=True)
        self._index.load(matches)
        blocks = [
            f"## {tool.name}\n{tool.description_text.strip()}\nParameters: {json.dumps(tool.input_schema, ensure_ascii=False)}"
            for tool in matches
        ]
        return ToolResult(tool_use_id="", content="Loaded. You can call these from your next step:\n\n" + "\n\n".join(blocks))
