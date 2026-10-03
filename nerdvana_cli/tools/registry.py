"""Tool registry assembly — collects all built-in tools."""

from __future__ import annotations

from typing import Any

from nerdvana_cli.core.tool import ToolRegistry
from nerdvana_cli.tools.ask_user_tool import AskUserTool
from nerdvana_cli.tools.bash_tool import BashTool, create_bash_tool
from nerdvana_cli.tools.file_tools import FileEditTool, FileReadTool, FileWriteTool
from nerdvana_cli.tools.parism_tool import ParismTool
from nerdvana_cli.tools.search_tools import GlobTool, GrepTool
from nerdvana_cli.tools.todo_tool import TodoWriteTool
from nerdvana_cli.tools.web_tools import WebFetchTool, WebSearchTool


def _register_skill_and_agent_tools(registry: ToolRegistry, settings: Any, task_registry: Any) -> None:
    """Register ActivateSkill (when some skill can be activated by the model), the Agent tool and, when enabled, the Workflow, memory and Advisor tools."""
    from nerdvana_cli.core.skills import SkillLoader
    from nerdvana_cli.mcp.skills import McpSkillFileTool
    from nerdvana_cli.tools.advisor_tool import AdvisorTool
    from nerdvana_cli.tools.agent_tool import AgentTool
    from nerdvana_cli.tools.anthropic_memory_tool import create_memory_tool
    from nerdvana_cli.tools.skill_tool import ActivateSkillTool

    loader = SkillLoader.from_settings(settings)
    loader.load_all()
    for tool in registry.all_tools():
        if isinstance(tool, McpSkillFileTool):
            loader.add_skills(tool.library.skills())
    if loader.model_skills():
        registry.register(ActivateSkillTool(loader))
    registry.register(AgentTool(settings=settings, task_registry=task_registry, parent_registry=registry))
    if getattr(getattr(settings, "workflow", None), "enabled", False) is True:
        from nerdvana_cli.tools.workflow_tool import WorkflowTool

        registry.register(WorkflowTool(settings=settings, parent_registry=registry))
    if (memory := create_memory_tool(settings)) is not None:
        registry.register(memory)
    if getattr(getattr(settings, "advisor", None), "enabled", False) is True:
        registry.register(AdvisorTool())


def create_tool_registry(
    parism_client:  Any    = None,
    mcp_tools:      Any    = None,
    settings:       Any    = None,
    task_registry:  Any    = None,
) -> ToolRegistry:
    """Create and populate the tool registry with all built-in tools."""
    from nerdvana_cli.core.task_state import TaskRegistry

    registry  = ToolRegistry()
    _task_reg = task_registry or TaskRegistry()

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

    # Task tracking tool
    registry.register(TodoWriteTool())

    # Clarifying questions: answerable only through an interactive front end,
    # so create_subagent_registry never hands this tool to a subagent.
    registry.register(AskUserTool())

    # Web tools — WebSearch raises ToolError at call time when BRAVE_API_KEY is absent
    registry.register(WebFetchTool())
    registry.register(WebSearchTool())

    if settings is not None:
        _register_skill_and_agent_tools(registry, settings, _task_reg)

    from nerdvana_cli.tools.team_tools import TaskGetTool, TaskStopTool
    registry.register(TaskGetTool(task_registry=_task_reg))
    registry.register(TaskStopTool(task_registry=_task_reg))

    if settings is not None:
        from nerdvana_cli.tools.swarm_tool import SwarmTool
        registry.register(SwarmTool(settings=settings, task_registry=_task_reg, parent_registry=registry))

    # Phase H: external project tools, gated on an explicit opt-in. A caller
    # that carries no setting gets the closed state, so a missing switch can
    # never widen what a session may reach.
    from nerdvana_cli.tools.external_project_tools import (
        ListQueryableProjectsTool,
        QueryExternalProjectTool,
        RegisterExternalProjectTool,
    )

    _ext_projects_enabled = bool(getattr(settings, "external_projects_enabled", False)) if settings else False
    if _ext_projects_enabled:
        registry.register(ListQueryableProjectsTool())
        registry.register(RegisterExternalProjectTool())
        registry.register(QueryExternalProjectTool())

    # LSP tools — registered only when a language server binary is installed
    from nerdvana_cli.core.lsp_client import LspClient
    from nerdvana_cli.tools.lsp import create_lsp_tools
    lsp = LspClient()
    if lsp.has_any_server():
        for lsp_tool in create_lsp_tools(lsp):
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


__all__ = [
    "create_tool_registry",
    "BashTool",
    "FileReadTool",
    "FileWriteTool",
    "FileEditTool",
    "GlobTool",
    "GrepTool",
]
