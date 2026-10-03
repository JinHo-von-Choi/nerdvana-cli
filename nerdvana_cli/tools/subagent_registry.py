"""The restricted tool registry a sub-agent works with."""

from __future__ import annotations

from typing import Any

from nerdvana_cli.core.tool import BaseTool, ToolCategory, ToolRegistry
from nerdvana_cli.tools.bash_tool import create_bash_tool
from nerdvana_cli.tools.file_tools import FileEditTool, FileReadTool, FileWriteTool
from nerdvana_cli.tools.search_tools import GlobTool, GrepTool

# Never handed to a subagent: spawning, background-task control and questions
# to the user all belong to the top-level session.
_SUBAGENT_EXCLUDED: frozenset[str] = frozenset({"Agent", "Swarm", "TaskGet", "TaskStop", "AskUser", "ToolSearch"})

# ``allowed_tools`` entry granting every tool that only reads.
READ_ONLY_TOKEN = "@read"
_READ_ONLY_CATEGORIES: frozenset[ToolCategory] = frozenset({ToolCategory.READ, ToolCategory.SYMBOLIC})


def create_subagent_registry(
    settings:       Any                          = None,
    mcp_tools:      Any                          = None,
    allowed_tools:  list[str] | None             = None,
    parent_tools:   list[BaseTool[Any]] | None   = None,
) -> ToolRegistry:
    """Create a restricted tool registry for subagent use.

    The standard file, search and shell tools are always candidates. With
    *parent_tools* (the session's own registry), its LSP, symbol, web, todo and
    MCP tools are candidates too, sharing the parent's instances so no second
    language server starts. Spawning, task control, AskUser and other META
    tools are never included.

    ``allowed_tools`` of None or ``["*"]`` admits every candidate. Otherwise a
    candidate is admitted when its name is listed, or when ``"@read"`` is
    listed and its category is READ or SYMBOLIC. MCP tools declare WRITE, so
    they are admitted only by name or by the wildcard.
    """
    _all: dict[str, BaseTool[Any]] = {}

    bash_tool = create_bash_tool()
    _all[bash_tool.name] = bash_tool
    for cls in (FileReadTool, FileWriteTool, FileEditTool, GlobTool, GrepTool):
        t = cls()
        _all[t.name] = t

    for tool in parent_tools or []:
        if tool.name not in _SUBAGENT_EXCLUDED and tool.category != ToolCategory.META:
            _all[tool.name] = tool

    if mcp_tools:
        for tool in mcp_tools:
            _all[tool.name] = tool

    wildcard = allowed_tools is None or allowed_tools == ["*"]
    registry = ToolRegistry()

    if wildcard:
        for tool in _all.values():
            registry.register(tool)
    else:
        allowed_set = set(allowed_tools or [])
        read_only   = READ_ONLY_TOKEN in allowed_set
        for name, tool in _all.items():
            if name in allowed_set or (read_only and tool.category in _READ_ONLY_CATEGORIES):
                registry.register(tool)

    return registry
