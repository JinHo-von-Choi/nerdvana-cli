"""Text and data helpers of the workflow engine: interpolation, JSON extraction and schema checks.

Author: 최진호
Date:   2026-10-03

A workflow step is a template. ``${inputs.name}`` is a workflow input, ``${steps.<id>.output}`` the text a
finished step produced, ``${steps.<id>.items}`` the list it produced, and inside a ``foreach`` step
``${item}`` (``${item.key}`` for a mapping) and ``${item_index}`` name the element being handled. A
reference that names nothing is an error, so a typo stops the run before any agent is paid for.
"""

from __future__ import annotations

import json
import re
import shlex
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from nerdvana_cli.utils.schema_check import _type_ok

REFERENCE = re.compile(r"\$\{([^}]*)\}")
WHOLE     = re.compile(r"^\s*\$\{([^}]*)\}\s*$")
FENCE     = re.compile(r"```(?:json)?\s*\n(.*?)```", re.DOTALL | re.IGNORECASE)

STEP_ATTRIBUTES = ("output", "items")


class WorkflowError(ValueError):
    """A workflow definition, its inputs or its template cannot be used."""


@dataclass
class Scope:
    """What a template can refer to while one step is rendered."""

    inputs:     Mapping[str, Any]
    steps:      Mapping[str, Mapping[str, Any]]        # step id -> {"output": str, "items": list}
    item:       Any                                    = None
    index:      int                                    = 0
    in_foreach: bool                                   = False


def references(template: str) -> list[str]:
    """The expressions of every ``${...}`` in *template*, in order."""
    return [match.strip() for match in REFERENCE.findall(template)]


def whole_reference(template: str) -> str:
    """The expression when *template* is exactly one ``${...}``; an empty string otherwise."""
    match = WHOLE.match(template)
    return match.group(1).strip() if match else ""


def reference_problem(expression: str, inputs: set[str], steps: set[str], in_foreach: bool) -> str:
    """Why *expression* can never resolve, or an empty string when it can."""
    parts = expression.split(".")
    head  = parts[0]
    if head == "inputs":
        return "" if len(parts) == 2 and parts[1] in inputs else f"unknown input in ${{{expression}}}"
    if head == "steps":
        if len(parts) != 3 or parts[1] not in steps or parts[2] not in STEP_ATTRIBUTES:
            return f"${{{expression}}} must be steps.<step id>.output or steps.<step id>.items of a step of this workflow"
        return ""
    if head in ("item", "item_index"):
        return "" if in_foreach else f"${{{expression}}} is only available in a step with foreach"
    return f"unknown reference ${{{expression}}}"


def _walk(value: Any, keys: list[str], expression: str) -> Any:
    """Follow *keys* into a mapping or list."""
    for key in keys:
        if isinstance(value, Mapping) and key in value:
            value = value[key]
        elif isinstance(value, list) and key.isdigit() and int(key) < len(value):
            value = value[int(key)]
        else:
            raise WorkflowError(f"${{{expression}}} does not exist")
    return value


def resolve(expression: str, scope: Scope) -> Any:
    """The value *expression* refers to in *scope*; WorkflowError when it refers to nothing."""
    parts = expression.strip().split(".")
    head  = parts[0]
    if head == "inputs" and len(parts) == 2 and parts[1] in scope.inputs:
        return scope.inputs[parts[1]]
    if head == "steps" and len(parts) == 3 and parts[1] in scope.steps and parts[2] in STEP_ATTRIBUTES:
        return scope.steps[parts[1]][parts[2]]
    if head == "item" and scope.in_foreach:
        return _walk(scope.item, parts[1:], expression)
    if head == "item_index" and scope.in_foreach and len(parts) == 1:
        return scope.index
    raise WorkflowError(f"${{{expression}}} does not exist")


def as_text(value: Any) -> str:
    """A value as it reads inside a prompt: text as it is, everything else as JSON."""
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)


def render(template: str, scope: Scope, shell: bool = False) -> str:
    """*template* with every reference replaced; with *shell* each value is quoted for a shell command line."""

    def _replace(match: re.Match[str]) -> str:
        text = as_text(resolve(match.group(1), scope))
        return shlex.quote(text) if shell else text

    return REFERENCE.sub(_replace, template)


def extract_json(text: str) -> Any:
    """The first JSON document in an agent's answer: a fenced block, the whole text, or the first object or array in it."""
    candidates = [match.group(1) for match in FENCE.finditer(text)] + [text]
    for candidate in candidates:
        try:
            return json.loads(candidate.strip())
        except ValueError:
            continue
    decoder = json.JSONDecoder()
    for index, char in enumerate(text):
        if char in "{[":
            try:
                return decoder.raw_decode(text[index:])[0]
            except ValueError:
                continue
    raise ValueError("no JSON found in the answer")


def validate_schema(value: Any, schema: Mapping[str, Any], path: str = "$") -> list[str]:
    """Problems of *value* against the JSON Schema subset ``type``, ``enum``, ``required``, ``properties``, ``items``, ``minItems``."""
    problems: list[str] = []
    declared = schema.get("type")
    if declared is not None and not _type_ok(value, declared):
        return [f"{path} must be {declared}, got {type(value).__name__}"]
    allowed = schema.get("enum")
    if isinstance(allowed, list) and value not in allowed:
        problems.append(f"{path} must be one of {allowed}")
    if isinstance(value, dict):
        problems += [f"{path}.{name} is required" for name in schema.get("required", []) if name not in value]
        for name, sub in (schema.get("properties") or {}).items():
            if name in value and isinstance(sub, Mapping):
                problems += validate_schema(value[name], sub, f"{path}.{name}")
    if isinstance(value, list):
        if len(value) < int(schema.get("minItems", 0)):
            problems.append(f"{path} needs at least {schema['minItems']} item(s)")
        if isinstance(schema.get("items"), Mapping):
            for index, element in enumerate(value):
                problems += validate_schema(element, schema["items"], f"{path}[{index}]")
    return problems


def items_of(value: Any) -> list[Any]:
    """The list a parsed JSON answer stands for: a bare array, the ``items`` array of an object, or the object itself."""
    if isinstance(value, list):
        return value
    if isinstance(value, dict) and isinstance(value.get("items"), list):
        return list(value["items"])
    return [] if value is None else [value]
