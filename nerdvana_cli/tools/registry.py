"""Tool registry assembly — collects all built-in tools."""

from __future__ import annotations

from nerdvana_cli.core.tool import ToolRegistry
from nerdvana_cli.tools.bash_tool import BashTool, create_bash_tool
from nerdvana_cli.tools.file_tools import FileEditTool, FileReadTool, FileWriteTool
from nerdvana_cli.tools.parism_tool import ParismTool
from nerdvana_cli.tools.search_tools import GlobTool, GrepTool


def create_tool_registry(parism_client=None, mcp_tools=None) -> ToolRegistry:
    """Create and populate the tool registry with all built-in tools."""
    registry = ToolRegistry()

    # MCP server tools
    if mcp_tools:
        for tool in mcp_tools:
            registry.register(tool)

    if parism_client is not None:
        parism_tool = ParismTool()
        parism_tool.set_client(parism_client)
        registry.register(parism_tool)

    registry.register(create_bash_tool())
    registry.register(FileReadTool())
    registry.register(FileWriteTool())
    registry.register(FileEditTool())
    registry.register(GlobTool())
    registry.register(GrepTool())

    return registry


__all__ = ["create_tool_registry", "BashTool", "FileReadTool", "FileWriteTool", "FileEditTool", "GlobTool", "GrepTool"]
