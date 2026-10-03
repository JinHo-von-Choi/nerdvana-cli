"""WorkflowTool: lets the model run a declared multi-agent workflow (core/workflow.py).

Author: 최진호
Date:   2026-10-03

Registered only when ``workflow.enabled`` is true. The model names a workflow and its inputs; the steps
come from the workflow file, never from the model.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, ClassVar

from nerdvana_cli.core.settings import NerdvanaSettings
from nerdvana_cli.core.tool import BaseTool, ToolCategory, ToolContext, ToolRegistry, ToolSideEffect
from nerdvana_cli.core.workflow import WorkflowError, discover, resolve_inputs
from nerdvana_cli.core.workflow_engine import COMPLETED, RunContext, RunReport, WorkflowRun
from nerdvana_cli.core.workflow_store import RunStore, new_run_id
from nerdvana_cli.tools.subagent_registry import create_subagent_registry
from nerdvana_cli.types import ToolResult


@dataclass
class WorkflowToolArgs:
    name:   str
    inputs: dict[str, Any] | None = None


def _error(message: str) -> ToolResult:
    return ToolResult(tool_use_id="", content=message, is_error=True)


def summarize(name: str, report: RunReport) -> ToolResult:
    """The model's view of a finished run: the final output and the run id, or why it stopped and how to resume."""
    if report.status == COMPLETED:
        note = f"[workflow {name}: run {report.run_id}, {report.ran_units} unit(s) run, {report.reused_units} reused, cost ${report.cost_usd:.4f}]"
        return ToolResult(tool_use_id="", content=f"{report.output}\n\n{note}")
    return _error(
        f"Workflow {name} {report.status}: {report.error}\nRun id: {report.run_id}. "
        f"The finished units are kept; resume with: nerdvana workflow run {name} --resume {report.run_id}"
    )


class WorkflowTool(BaseTool[WorkflowToolArgs]):
    """Run a named workflow from .nerdvana/workflows and return its final output."""

    name             = "Workflow"
    description_text = (
        "Run a declared multi-agent workflow (a YAML file under .nerdvana/workflows or ~/.nerdvana/workflows, or a bundled one "
        "such as review-fanout). Its steps run in dependency order, agents in parallel, read-only unless the workflow allows "
        "writing, under one cost ceiling. Returns the final step's output and a run id."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "name":   {"type": "string", "description": "Workflow name."},
            "inputs": {"type": "object", "description": "Values for the workflow's inputs, by name."},
        },
        "required": ["name"],
    }
    is_concurrency_safe    = False
    args_class             = WorkflowToolArgs
    category               = ToolCategory.META
    side_effects           = ToolSideEffect.EXTERNAL
    tags: ClassVar[frozenset[str]] = frozenset({"agent"})
    requires_confirmation  = True

    def __init__(self, settings: NerdvanaSettings, parent_registry: ToolRegistry | None = None) -> None:
        self._settings        = settings
        self._parent_registry = parent_registry

    async def call(self, args: WorkflowToolArgs, context: ToolContext, can_use_tool: Any, on_progress: Any = None) -> ToolResult:
        workflows, _ = discover(context.cwd)
        workflow     = workflows.get(args.name)
        if workflow is None:
            return _error(f"Unknown workflow '{args.name}'. Available: {', '.join(sorted(workflows)) or 'none'}")
        try:
            inputs = resolve_inputs(workflow, args.inputs or {})
        except WorkflowError as exc:
            return _error(str(exc))
        shared   = context.state.get("budget")
        fraction = self._settings.session.subagent_budget_fraction
        reserved = shared[0].reserve(fraction, shared[1]()) if shared and shared[0].limit > 0 and fraction > 0 else None
        run      = RunContext(
            settings=self._settings, registry_factory=create_subagent_registry, cwd=context.cwd, store=RunStore(new_run_id()),
            ceiling=reserved.amount if reserved else 0.0, confirm=context.confirm, absorb=context.state.get("absorb"),
            factories=context.state.get("loop_factories"), parent_session_id=str(context.state.get("session_id", "")),
            parent_tools=self._parent_registry.all_tools() if self._parent_registry else None,
        )
        execution = WorkflowRun(workflow, inputs, run)
        try:
            report = await execution.run()
        except WorkflowError as exc:
            return _error(str(exc))
        finally:
            if reserved is not None and shared is not None:
                shared[0].settle(reserved, execution.spent)
        return summarize(workflow.name, report)
