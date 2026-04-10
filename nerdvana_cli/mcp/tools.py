"""MCP tool -> BaseTool adapter."""

from __future__ import annotations

import re
from typing import Any

from nerdvana_cli.core.tool import BaseTool, ToolContext
from nerdvana_cli.mcp.client import McpClient
from nerdvana_cli.types import ToolResult


def _normalize_server_name(name: str) -> str:
    """Normalize server name: replace hyphens with underscores."""
    return re.sub(r"[^a-zA-Z0-9_]", "_", name)


class McpToolAdapter(BaseTool[dict]):
    """Wraps a single MCP server tool as a BaseTool for the registry."""

    def __init__(
        self,
        server_name: str,
        tool_def: dict[str, Any],
        client: McpClient,
    ) -> None:
        normalized          = _normalize_server_name(server_name)
        raw_tool_name       = tool_def.get("name", "unknown")
        tool_name           = _normalize_server_name(raw_tool_name)
        self.name           = f"mcp__{normalized}__{tool_name}"
        self.description_text = tool_def.get("description", "")
        self.input_schema   = tool_def.get("inputSchema", {})
        self._client        = client
        self._server_name   = server_name
        self._tool_name          = raw_tool_name
        self.is_read_only        = False
        self.is_concurrency_safe = True
        self.is_destructive      = False

    async def call(
        self,
        args: dict,
        context: ToolContext,
        can_use_tool: Any,
        on_progress: Any = None,
    ) -> ToolResult:
        """Proxy the call to the MCP server."""
        try:
            result  = await self._client.call_tool(self._tool_name, args)
            content = result.get("content", [])
            text    = "\n".join(
                item.get("text", "") for item in content if isinstance(item, dict)
            )
            return ToolResult(
                tool_use_id="",
                content=text or str(result),
                is_error=result.get("isError", False),
            )
        except Exception as exc:
            return ToolResult(
                tool_use_id="",
                content=f"MCP tool error: {exc}",
                is_error=True,
            )

    def prompt(self) -> str:
        schema_str = ""
        if self.input_schema:
            import json
            schema_str = f"\n\nInput schema:\n```json\n{json.dumps(self.input_schema, indent=2)}\n```"
        return f"## {self.name}\n\n{self.description_text}{schema_str}"
