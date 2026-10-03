"""Declarative multi-agent workflows: the file format, its validation and where workflows are found.

Author: 최진호
Date:   2026-10-03

A workflow is a YAML file, not code a model writes: the same file always runs the same steps in the same
order. ``<project>/.nerdvana/workflows/*.yml``, ``~/.nerdvana/workflows/*.yml`` and the bundled ones are
searched, in that order of precedence.

    name:        review-fanout
    description: Review the changed files in parallel.
    inputs:
      base: {default: HEAD, description: Revision to compare with}
    steps:
      - id: files
        kind: verify                       # a shell command; a non-zero exit stops the run
        command: git diff --name-only ${inputs.base}
      - id: review
        agent: code-reviewer
        foreach: ${steps.files.items}      # one agent per element, in parallel
        prompt: Review ${item}.
        output: json
        schema: {type: object, required: [items], properties: {items: {type: array}}}

Step kinds: ``agent`` (default), ``verify`` and ``cross_check``. Which step may run when is the DAG made
by ``needs`` and by every ``${steps.<id>...}`` a step refers to. Execution is in ``core/workflow_engine.py``.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped,unused-ignore]

from nerdvana_cli.core import paths as core_paths
from nerdvana_cli.core.workflow_text import WorkflowError, reference_problem, references, whole_reference

__all__ = [
    "Workflow", "WorkflowError", "InputSpec", "Step", "discover", "load_workflow", "parse_workflow", "resolve_inputs",
    "KINDS", "ORIGIN_BUNDLED", "ORIGIN_PROJECT", "ORIGIN_USER",
]

KINDS           = ("agent", "verify", "cross_check")
OUTPUT_FORMATS  = ("text", "json")
ORIGIN_PROJECT  = "project"
ORIGIN_USER     = "user"
ORIGIN_BUNDLED  = "bundled"
IDENTIFIER      = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
DEFAULT_AGENT   = "general-purpose"
DEFAULT_REVIEWERS = 3
MAX_REVIEWERS   = 9

_COMMON_KEYS = {"id", "kind", "needs"}
_KIND_KEYS: dict[str, set[str]] = {
    "agent":       {"agent", "prompt", "foreach", "max_turns", "category", "model", "write_scope", "output", "schema"},
    "verify":      {"command", "timeout"},
    "cross_check": {"agent", "prompt", "claims", "reviewers", "max_turns", "category", "model"},
}


@dataclass(frozen=True)
class InputSpec:
    """One named parameter of a workflow."""

    name:        str
    default:     Any  = None
    required:    bool = False
    description: str  = ""


@dataclass(frozen=True)
class Step:
    """One stage of a workflow; the fields a kind does not use keep their defaults."""

    id:          str
    kind:        str                    = "agent"
    needs:       tuple[str, ...]        = ()
    agent:       str                    = DEFAULT_AGENT
    prompt:      str                    = ""
    foreach:     str                    = ""
    max_turns:   int                    = 0
    category:    str                    = ""
    model:       str                    = ""
    write_scope: str | tuple[str, ...]  = "none"
    output:      str                    = "text"
    schema:      Mapping[str, Any] | None = None
    command:     str                    = ""
    timeout:     int                    = 0
    claims:      str                    = ""
    reviewers:   int                    = DEFAULT_REVIEWERS


@dataclass(frozen=True)
class Workflow:
    """A validated workflow."""

    name:        str
    description: str
    inputs:      tuple[InputSpec, ...]
    steps:       tuple[Step, ...]
    result:      str                    # id of the step whose output is the workflow's answer
    allow_write: bool                   = False
    source:      str                    = ""
    origin:      str                    = ORIGIN_PROJECT


def _texts(step: Step) -> list[str]:
    """Every template of a step."""
    return [step.prompt, step.command, step.foreach, step.claims]


def _identifier(value: Any, what: str) -> str:
    if not isinstance(value, str) or not IDENTIFIER.match(value):
        raise WorkflowError(f"{what} must be letters, digits, '-' or '_' and start with a letter, got {value!r}")
    return value


def _count(raw: Mapping[str, Any], key: str, default: int, where: str) -> int:
    value = raw.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise WorkflowError(f"{where}: {key} must be a non-negative whole number")
    return int(value)


def _write_scope(raw: Any, where: str) -> str | tuple[str, ...]:
    """``none``, ``project`` or a list of project paths."""
    if raw in ("none", "project"):
        return str(raw)
    if isinstance(raw, list) and raw and all(isinstance(entry, str) and entry.strip() for entry in raw):
        return tuple(raw)
    raise WorkflowError(f"{where}: write_scope must be 'none', 'project' or a list of paths")


def _check_kind_fields(raw: Mapping[str, Any], kind: str, where: str) -> None:
    unknown = sorted(set(raw) - _COMMON_KEYS - _KIND_KEYS[kind])
    if unknown:
        raise WorkflowError(f"{where}: {', '.join(unknown)} does not apply to a {kind} step")


def _parse_step(raw: Any, index: int) -> Step:
    """One entry of ``steps``, checked for its kind."""
    if not isinstance(raw, Mapping):
        raise WorkflowError(f"step {index + 1}: expected a mapping")
    step_id = _identifier(raw.get("id"), f"step {index + 1}: id")
    where   = f"step {step_id}"
    kind    = raw.get("kind", "agent")
    if kind not in KINDS:
        raise WorkflowError(f"{where}: kind must be one of {', '.join(KINDS)}")
    _check_kind_fields(raw, kind, where)
    needs = raw.get("needs") or []
    if not isinstance(needs, list):
        raise WorkflowError(f"{where}: needs must be a list of step ids")
    fields: dict[str, Any] = {
        "id": step_id, "kind": kind, "needs": tuple(_identifier(need, f"{where}: needs entry") for need in needs),
        "agent":     str(raw.get("agent", DEFAULT_AGENT)),
        "prompt":    str(raw.get("prompt", "") or ""),
        "foreach":   str(raw.get("foreach", "") or ""),
        "max_turns": _count(raw, "max_turns", 0, where),
        "category":  str(raw.get("category", "") or ""),
        "model":     str(raw.get("model", "") or ""),
        "command":   str(raw.get("command", "") or ""),
        "timeout":   _count(raw, "timeout", 0, where),
        "claims":    str(raw.get("claims", "") or ""),
        "output":    raw.get("output", "text"),
        "schema":    raw.get("schema"),
        "write_scope": _write_scope(raw.get("write_scope", "none"), where),
        "reviewers": _count(raw, "reviewers", DEFAULT_REVIEWERS, where),
    }
    step = Step(**fields)
    _check_required(step, where)
    return step


def _check_required(step: Step, where: str) -> None:
    """What each kind of step must and must not carry."""
    if step.kind == "agent":
        if not step.prompt.strip():
            raise WorkflowError(f"{where}: an agent step needs a prompt")
        if step.output not in OUTPUT_FORMATS:
            raise WorkflowError(f"{where}: output must be one of {', '.join(OUTPUT_FORMATS)}")
        if step.output == "json" and not isinstance(step.schema, Mapping):
            raise WorkflowError(f"{where}: output: json needs a schema")
        if step.output == "text" and step.schema is not None:
            raise WorkflowError(f"{where}: schema needs output: json")
        if step.foreach and not whole_reference(step.foreach):
            raise WorkflowError(f"{where}: foreach must be one reference such as ${{steps.<id>.items}}")
    elif step.kind == "verify":
        if not step.command.strip():
            raise WorkflowError(f"{where}: a verify step needs a command")
    else:
        if not whole_reference(step.claims):
            raise WorkflowError(f"{where}: claims must be one reference such as ${{steps.<id>.items}}")
        if not 1 <= step.reviewers <= MAX_REVIEWERS:
            raise WorkflowError(f"{where}: reviewers must be between 1 and {MAX_REVIEWERS}")


def _parse_inputs(raw: Any) -> tuple[InputSpec, ...]:
    """``inputs`` maps a name to a default value, or to ``{default, description}``; no default means required."""
    if raw is None:
        return ()
    if not isinstance(raw, Mapping):
        raise WorkflowError("inputs must be a mapping of names to defaults")
    specs: list[InputSpec] = []
    for name, value in raw.items():
        _identifier(name, "input name")
        if isinstance(value, Mapping):
            unknown = sorted(set(value) - {"default", "description"})
            if unknown:
                raise WorkflowError(f"input {name}: {', '.join(unknown)} is not a known field")
            specs.append(InputSpec(name, value.get("default"), "default" not in value, str(value.get("description", "") or "")))
        else:
            specs.append(InputSpec(name, value, value is None))
    return tuple(specs)


def _link_steps(steps: list[Step], inputs: tuple[InputSpec, ...], allow_write: bool) -> tuple[Step, ...]:
    """Validate references and the write rule, and add each step's referenced steps to its ``needs``."""
    ids = [step.id for step in steps]
    for step_id in ids:
        if ids.count(step_id) > 1:
            raise WorkflowError(f"step id {step_id} appears twice")
    names, linked = {spec.name for spec in inputs}, []
    for step in steps:
        where, needed = f"step {step.id}", list(step.needs)
        if step.write_scope != "none" and not allow_write:
            raise WorkflowError(f"{where}: write_scope needs allow_write: true in the workflow")
        for text in _texts(step):
            for expression in references(text):
                problem = reference_problem(expression, names, set(ids), bool(step.foreach))
                if problem:
                    raise WorkflowError(f"{where}: {problem}")
                if expression.startswith("steps."):
                    needed.append(expression.split(".")[1])
        for need in needed:
            if need not in ids or need == step.id:
                raise WorkflowError(f"{where}: needs {need}, which is not another step of this workflow")
        linked.append(_with_needs(step, needed))
    _check_acyclic(linked)
    return tuple(linked)


def _with_needs(step: Step, needed: list[str]) -> Step:
    """*step* with its dependencies listed once each, declared ones first."""
    return replace(step, needs=tuple(dict.fromkeys(needed)))


def _check_acyclic(steps: list[Step]) -> None:
    """Raise WorkflowError when the dependencies of the steps form a cycle."""
    remaining = {step.id: set(step.needs) for step in steps}
    while remaining:
        free = [step_id for step_id, needs in remaining.items() if not needs]
        if not free:
            raise WorkflowError(f"steps depend on each other in a cycle: {', '.join(sorted(remaining))}")
        for step_id in free:
            del remaining[step_id]
        for needs in remaining.values():
            needs.difference_update(free)


def parse_workflow(data: Any, source: str = "", origin: str = ORIGIN_PROJECT) -> Workflow:
    """A Workflow from parsed YAML; WorkflowError names the first thing wrong."""
    if not isinstance(data, Mapping):
        raise WorkflowError("the file must hold a mapping")
    unknown = sorted(set(data) - {"name", "description", "inputs", "steps", "allow_write", "result"})
    if unknown:
        raise WorkflowError(f"unknown key(s): {', '.join(unknown)}")
    name = _identifier(data.get("name"), "name")
    raw_steps = data.get("steps")
    if not isinstance(raw_steps, list) or not raw_steps:
        raise WorkflowError("steps must be a non-empty list")
    allow_write = data.get("allow_write", False)
    if not isinstance(allow_write, bool):
        raise WorkflowError("allow_write must be true or false")
    inputs = _parse_inputs(data.get("inputs"))
    steps  = _link_steps([_parse_step(raw, index) for index, raw in enumerate(raw_steps)], inputs, allow_write)
    result = str(data.get("result") or steps[-1].id)
    if result not in {step.id for step in steps}:
        raise WorkflowError(f"result names {result}, which is not a step")
    return Workflow(name, str(data.get("description", "") or ""), inputs, steps, result, allow_write, source, origin)


def load_workflow(path: Path, origin: str = ORIGIN_PROJECT) -> Workflow:
    """Read and validate one workflow file; WorkflowError carries the file name."""
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        return parse_workflow(data, str(path), origin)
    except (OSError, yaml.YAMLError) as exc:
        raise WorkflowError(f"{path}: {exc}") from exc
    except WorkflowError as exc:
        raise WorkflowError(f"{path}: {exc}") from exc


def bundled_dir() -> Path:
    """The workflows that ship with the package."""
    return Path(__file__).resolve().parent.parent / "resources" / "workflows"


def discover(cwd: str) -> tuple[dict[str, Workflow], list[str]]:
    """Every usable workflow by name, and a message for each file that could not be loaded.

    A project workflow replaces a user workflow of the same name, which replaces a bundled one.
    """
    found:    dict[str, Workflow] = {}
    problems: list[str]           = []
    tiers = (
        (ORIGIN_BUNDLED, bundled_dir()),
        (ORIGIN_USER,    core_paths.user_workflows_dir()),
        (ORIGIN_PROJECT, Path(cwd) / ".nerdvana" / "workflows"),
    )
    for origin, directory in tiers:
        if not directory.is_dir():
            continue
        for path in sorted([*directory.glob("*.yml"), *directory.glob("*.yaml")]):
            try:
                workflow = load_workflow(path, origin)
            except WorkflowError as exc:
                problems.append(str(exc))
                continue
            found[workflow.name] = workflow
    return found, problems


def resolve_inputs(workflow: Workflow, given: Mapping[str, Any]) -> dict[str, Any]:
    """The inputs a run uses: what was given over the defaults; WorkflowError for an unknown or a missing one."""
    known   = {spec.name: spec for spec in workflow.inputs}
    unknown = sorted(set(given) - set(known))
    if unknown:
        raise WorkflowError(f"unknown input(s): {', '.join(unknown)}; the workflow takes: {', '.join(known) or 'none'}")
    values: dict[str, Any] = {}
    for name, spec in known.items():
        if name in given:
            values[name] = given[name]
        elif spec.required:
            raise WorkflowError(f"input {name} is required")
        else:
            values[name] = spec.default
    return values
