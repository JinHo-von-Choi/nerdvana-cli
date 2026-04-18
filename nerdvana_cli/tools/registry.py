"""Tool registry assembly — collects all built-in tools."""

from __future__ import annotations

from typing import Any

from nerdvana_cli.core.tool import BaseTool, ToolRegistry
from nerdvana_cli.tools.bash_tool import BashTool, create_bash_tool
from nerdvana_cli.tools.file_tools import FileEditTool, FileReadTool, FileWriteTool
from nerdvana_cli.tools.parism_tool import ParismTool
from nerdvana_cli.tools.search_tools import GlobTool, GrepTool


def create_tool_registry(
    parism_client:  Any    = None,
    mcp_tools:      Any    = None,
    settings:       Any    = None,
    task_registry:  Any    = None,
    team_registry:  Any    = None,
) -> ToolRegistry:
    """Create and populate the tool registry with all built-in tools."""
    from nerdvana_cli.core.task_state import TaskRegistry
    from nerdvana_cli.core.team import TeamRegistry

    registry  = ToolRegistry()
    _task_reg = task_registry or TaskRegistry()
    _team_reg = team_registry or TeamRegistry()

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

    if settings is not None:
        from nerdvana_cli.tools.agent_tool import AgentTool
        registry.register(AgentTool(settings=settings, task_registry=_task_reg))

    from nerdvana_cli.tools.team_tools import (
        SendMessageTool,
        TaskGetTool,
        TaskStopTool,
        TeamCreateTool,
    )
    registry.register(TeamCreateTool(team_registry=_team_reg))
    registry.register(SendMessageTool(team_registry=_team_reg))
    registry.register(TaskGetTool(task_registry=_task_reg))
    registry.register(TaskStopTool(task_registry=_task_reg))

    if settings is not None:
        from nerdvana_cli.tools.swarm_tool import SwarmTool
        registry.register(SwarmTool(settings=settings, task_registry=_task_reg))

    # Phase H: external project tools (always registered; subprocess-isolated)
    from nerdvana_cli.tools.external_project_tools import (
        ListQueryableProjectsTool,
        QueryExternalProjectTool,
        RegisterExternalProjectTool,
    )

    _ext_projects_enabled = getattr(settings, "external_projects_enabled", True) if settings else True
    if _ext_projects_enabled:
        registry.register(ListQueryableProjectsTool())
        registry.register(RegisterExternalProjectTool())
        registry.register(QueryExternalProjectTool())

    # LSP tools — registered only when a language server binary is installed
    from nerdvana_cli.core.lsp_client import LspClient
    lsp = LspClient()
    if lsp.has_any_server():
        for lsp_tool in lsp.available_tools():
            registry.register(lsp_tool)

        # Phase D: semantic symbol tools
        from nerdvana_cli.core.code_editor import CodeEditor
        from nerdvana_cli.core.symbol import LanguageServerSymbolRetriever
        from nerdvana_cli.tools.symbol_tools import create_symbol_tools

        retriever = LanguageServerSymbolRetriever(client=lsp)
        editor    = CodeEditor(project_root=lsp._project_root)   # noqa: SLF001
        for sym_tool in create_symbol_tools(
            client=lsp, retriever=retriever, editor=editor,
        ):
            registry.register(sym_tool)

    return registry


def create_subagent_registry(
    settings:       Any                = None,
    mcp_tools:      Any                = None,
    allowed_tools:  list[str] | None   = None,
) -> ToolRegistry:
    """Create a restricted tool registry for subagent use.

    Subagents get standard tools filtered by allowed_tools but NOT AgentTool or
    team tools (no recursive spawning, no team management from subagents).

    If allowed_tools is None or ["*"], all standard tools are included.
    Otherwise only tools whose name appears in allowed_tools are registered.
    """
    _all: dict[str, BaseTool[Any]] = {}

    bash_tool = create_bash_tool()
    _all[bash_tool.name] = bash_tool
    for cls in (FileReadTool, FileWriteTool, FileEditTool, GlobTool, GrepTool):
        t = cls()
        _all[t.name] = t

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
        for name, tool in _all.items():
            if name in allowed_set:
                registry.register(tool)

    return registry


__all__ = [
    "create_tool_registry",
    "create_subagent_registry",
    "BashTool",
    "FileReadTool",
    "FileWriteTool",
    "FileEditTool",
    "GlobTool",
    "GrepTool",
]
