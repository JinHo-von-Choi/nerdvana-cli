"""`nerdvana workflow ...`: list, show and run declared multi-agent workflows.

Author: 최진호
Date:   2026-10-03

    nerdvana workflow list
    nerdvana workflow show NAME
    nerdvana workflow run NAME [--input k=v]... [--max-cost-usd X] [--resume RUN_ID] [--approval-mode MODE]

Exit codes of ``run``: 0 completed, 1 failed, 2 invalid workflow, inputs or configuration, 3 stopped by the
cost ceiling. The behavior is in ``core/delegation/workflow_engine.py``; see docs/workflows.md.
"""

from __future__ import annotations

import asyncio
import os
from typing import NoReturn

import typer
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from nerdvana_cli.core.delegation.workflow import Workflow, WorkflowError, discover, resolve_inputs
from nerdvana_cli.core.delegation.workflow_engine import COMPLETED, STOPPED, RunContext, RunReport, WorkflowRun
from nerdvana_cli.core.delegation.workflow_store import RunStore, new_run_id

console     = Console()
err_console = Console(stderr=True)

workflow_app = typer.Typer(
    name           = "workflow",
    help           = "List and run declared multi-agent workflows.",
    add_completion = False,
)


def _fail(message: str, code: int = 2) -> NoReturn:
    err_console.print(f"[red]Error: {escape(message)}[/red]")
    raise typer.Exit(code)


def _find(name: str, cwd: str) -> Workflow:
    """The workflow called *name*; the command ends when there is none."""
    workflows, _ = discover(cwd)
    if name not in workflows:
        _fail(f"unknown workflow '{name}'; available: {', '.join(sorted(workflows)) or 'none'}")
    return workflows[name]


def _read_inputs(values: list[str]) -> dict[str, str]:
    """``k=v`` options as a mapping."""
    given: dict[str, str] = {}
    for value in values:
        name, separator, text = value.partition("=")
        if not separator or not name:
            _fail(f"--input expects name=value, got '{value}'")
        given[name] = text
    return given


@workflow_app.command("list")
def workflow_list() -> None:
    """List the workflows found in the project, in ~/.nerdvana/workflows and bundled with nerdvana."""
    workflows, problems = discover(os.getcwd())
    if workflows:
        table = Table("Name", "Origin", "Steps", "Description")
        for name in sorted(workflows):
            workflow = workflows[name]
            table.add_row(escape(name), workflow.origin, str(len(workflow.steps)), escape(workflow.description))
        console.print(table)
    else:
        console.print("No workflows found.")
    for problem in problems:
        err_console.print(f"[yellow]Skipped: {escape(problem)}[/yellow]")


@workflow_app.command("show")
def workflow_show(name: str = typer.Argument(..., help="Workflow name")) -> None:
    """Show a workflow's inputs and steps."""
    workflow = _find(name, os.getcwd())
    console.print(f"[bold]{escape(workflow.name)}[/bold] ({workflow.origin}: {escape(workflow.source)})")
    if workflow.description:
        console.print(escape(workflow.description))
    console.print(f"Writing allowed: {'yes' if workflow.allow_write else 'no (every agent is read-only)'}; result: step {escape(workflow.result)}")
    for spec in workflow.inputs:
        default = "required" if spec.required else f"default {escape(str(spec.default))}"
        console.print(f"  input [cyan]{escape(spec.name)}[/cyan] ({default}) {escape(spec.description)}")
    for step in workflow.steps:
        detail = [f"agent {step.agent}"] if step.kind != "verify" else [f"command {step.command}"]
        if step.foreach:
            detail.append(f"foreach {step.foreach}")
        if step.kind == "cross_check":
            detail.append(f"{step.reviewers} reviewers")
        if step.needs:
            detail.append(f"needs {', '.join(step.needs)}")
        console.print(f"  step [cyan]{escape(step.id)}[/cyan] {step.kind}: {escape('; '.join(detail))}")


def _start(workflow: Workflow, given: dict[str, str], resume: str) -> tuple[dict[str, str], RunStore]:
    """The inputs and the run directory of this run; a resumed run keeps its stored inputs unless overridden."""
    try:
        store = RunStore(resume or new_run_id())
        if resume:
            meta = store.read_meta()
            if meta.get("workflow") != workflow.name:
                raise WorkflowError(f"run {resume} belongs to workflow '{meta.get('workflow')}', not '{workflow.name}'")
            given = {**meta.get("inputs", {}), **given}
        return resolve_inputs(workflow, given), store
    except WorkflowError as exc:
        _fail(str(exc))


def _print_report(report: RunReport, name: str) -> None:
    if report.output:
        console.print(escape(report.output))
    line = f"{report.status}: run {report.run_id}, {report.ran_units} unit(s) run, {report.reused_units} reused, cost ${report.cost_usd:.4f}"
    if report.status == COMPLETED:
        console.print(f"[dim]{escape(line)}[/dim]")
        return
    err_console.print(f"[red]{escape(report.error)}[/red]")
    err_console.print(f"[yellow]{escape(line)}[/yellow]")
    err_console.print(f"Resume with: nerdvana workflow run {escape(name)} --resume {report.run_id}")


@workflow_app.command("run")
def workflow_run(
    name:          str       = typer.Argument(..., help="Workflow name"),
    input_values:  list[str] = typer.Option([], "--input", "-i", help="Workflow input as name=value (repeatable)"),  # noqa: B008
    max_cost_usd:  float     = typer.Option(0.0, "--max-cost-usd", help="Ceiling for the whole run in USD (0 = session.max_cost_usd, or none)"),
    resume:        str       = typer.Option("", "--resume", help="Run id to continue: units that finished with unchanged inputs are not run again"),
    approval_mode: str       = typer.Option("", "--approval-mode", help="Preset mode for the agents: default | auto_edit | yolo | plan"),
    config:        str       = typer.Option("", "--config", "-c", help="Config file path"),
    cwd:           str       = typer.Option("", "--cwd", help="Working directory"),
) -> None:
    """Run a workflow and print its final output.

    Exit codes: 0 completed, 1 failed, 2 invalid workflow, inputs or configuration, 3 stopped by the cost ceiling.
    """
    from nerdvana_cli.cli.bootstrap import loop_factories
    from nerdvana_cli.cli.runtime import APPROVAL_MODE_MAP, enforce_managed_policy, load_settings, resolve_run_provider
    from nerdvana_cli.tools.subagent_registry import create_subagent_registry

    if approval_mode and approval_mode not in APPROVAL_MODE_MAP:
        _fail(f"--approval-mode must be one of {', '.join(APPROVAL_MODE_MAP)}")
    settings     = load_settings(config or None)
    settings.cwd = cwd or os.getcwd()
    if approval_mode:
        settings.session.default_mode = APPROVAL_MODE_MAP[approval_mode][0]
    enforce_managed_policy(settings)
    workflow       = _find(name, settings.cwd)
    inputs, store  = _start(workflow, _read_inputs(input_values), resume)
    provider, missing = resolve_run_provider(settings)
    if missing:
        _fail(f"No API key found for {provider}.")
    context = RunContext(
        settings=settings, registry_factory=create_subagent_registry, cwd=settings.cwd, store=store,
        ceiling=max_cost_usd or settings.session.max_cost_usd, factories=loop_factories(),
        progress=lambda message: err_console.print(f"[dim]{escape(message)}[/dim]"),
    )
    try:
        report = asyncio.run(WorkflowRun(workflow, inputs, context).run())
    except WorkflowError as exc:
        _fail(str(exc))
    _print_report(report, workflow.name)
    if report.status != COMPLETED:
        raise typer.Exit(3 if report.status == STOPPED else 1)
