"""MCP tool -> BaseTool adapter."""

from __future__ import annotations

import re
from typing import Any, ClassVar

from nerdvana_cli.core.tool import BaseTool, ToolCategory, ToolContext, ToolSideEffect
from nerdvana_cli.mcp.client import McpClient
from nerdvana_cli.types import ToolResult


def _hint(tool_def: dict[str, Any], key: str) -> bool | None:
    """The boolean MCP tool annotation *key*, or None when the server did not state one."""
    annotations = tool_def.get("annotations")
    value = annotations.get(key) if isinstance(annotations, dict) else None
    return value if isinstance(value, bool) else None


def _normalize_server_name(name: str) -> str:
    """Normalize server name: replace hyphens with underscores."""
    return re.sub(r"[^a-zA-Z0-9_]", "_", name)


class McpToolAdapter(BaseTool[dict[str, Any]]):
    """Wraps a single MCP server tool as a BaseTool for the registry.

    The server's annotations are hints from a third party, so they only ever make the tool
    stricter than the default: a tool runs next to other calls only when it declares itself
    read-only (``readOnlyHint``), and one that declares itself destructive
    (``destructiveHint``) asks before it runs (``McpDestructiveToolAdapter``). A tool that
    says nothing is serialized and treated as a state-changing external tool.
    """

    # MCP tools are treated as EXTERNAL write operations by default.
    category:              ClassVar[ToolCategory]   = ToolCategory.WRITE
    side_effects:          ClassVar[ToolSideEffect] = ToolSideEffect.EXTERNAL
    tags:                  ClassVar[frozenset[str]] = frozenset({"mcp"})
    requires_confirmation: ClassVar[bool]           = False
    # The remote server owns its schema; extra keys are its call to make.
    reject_unknown_args:   ClassVar[bool]           = False

    def __init__(
        self,
        server_name: str,
        tool_def: dict[str, Any],
        client: McpClient,
    ) -> None:
        normalized            = _normalize_server_name(server_name)
        raw_tool_name         = tool_def.get("name", "unknown")
        tool_name             = _normalize_server_name(raw_tool_name)
        self.name             = f"mcp__{normalized}__{tool_name}"
        self.description_text = tool_def.get("description", "")
        self.input_schema     = tool_def.get("inputSchema", {})
        self._client          = client
        self._server_name     = server_name
        self._tool_name       = raw_tool_name
        self.is_concurrency_safe = _hint(tool_def, "readOnlyHint") is True
        self.is_destructive      = _hint(tool_def, "destructiveHint") is True and _hint(tool_def, "readOnlyHint") is not True

    async def call(
        self,
        args: dict[str, Any],
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


class McpDestructiveToolAdapter(McpToolAdapter):
    """An MCP tool whose server declares it destructive; every call asks first."""

    category: ClassVar[ToolCategory] = ToolCategory.DESTRUCTIVE


def build_mcp_tool(server_name: str, tool_def: dict[str, Any], client: McpClient) -> McpToolAdapter:
    """The adapter that fits the annotations of *tool_def*."""
    destructive = _hint(tool_def, "destructiveHint") is True and _hint(tool_def, "readOnlyHint") is not True
    return (McpDestructiveToolAdapter if destructive else McpToolAdapter)(server_name, tool_def, client)
