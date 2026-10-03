"""Top-level validation of tool arguments against a tool's JSON Schema.

Author: 최진호
Date:   2026-10-03

Covers what models get wrong in practice: missing required arguments,
misspelled or invented argument names, and wrong primitive types. Nested
objects are not descended into; the tool's own ``validate_input`` owns those.
"""

from __future__ import annotations

from typing import Any

_JSON_TYPES: dict[str, tuple[type, ...]] = {
    "string":  (str,),
    "integer": (int,),
    "number":  (int, float),
    "boolean": (bool,),
    "array":   (list,),
    "object":  (dict,),
    "null":    (type(None),),
}


def _type_ok(value: Any, declared: str | list[str]) -> bool:
    names = [declared] if isinstance(declared, str) else list(declared)
    for name in names:
        expected = _JSON_TYPES.get(name)
        if expected is None:
            return True
        if isinstance(value, bool) and name in {"integer", "number"}:
            continue
        if isinstance(value, expected):
            return True
    return False


def validate_arguments(
    schema:         dict[str, Any],
    arguments:      Any,
    reject_unknown: bool = True,
) -> list[str]:
    """Return human-readable problems with *arguments*; empty when valid.

    Unknown keys are rejected when *reject_unknown* is true, unless the schema
    sets ``additionalProperties`` to true.
    """
    if not isinstance(arguments, dict):
        return [f"arguments must be an object, got {type(arguments).__name__}"]

    properties: dict[str, Any] = schema.get("properties") or {}
    required:   list[str]      = list(schema.get("required") or [])
    problems:   list[str]      = []

    missing = [name for name in required if name not in arguments]
    if missing:
        problems.append(f"missing required argument(s): {', '.join(missing)}")

    if properties and reject_unknown and schema.get("additionalProperties") is not True:
        unknown = sorted(set(arguments) - set(properties))
        if unknown:
            problems.append(
                f"unknown argument(s): {', '.join(unknown)}; expected: {', '.join(sorted(properties))}"
            )

    for name, value in arguments.items():
        spec = properties.get(name)
        if not isinstance(spec, dict):
            continue
        declared = spec.get("type")
        if declared is not None and not _type_ok(value, declared):
            problems.append(f"argument '{name}' must be {declared}, got {type(value).__name__}")
            continue
        allowed = spec.get("enum")
        if isinstance(allowed, list) and value not in allowed:
            problems.append(f"argument '{name}' must be one of {allowed}, got {value!r}")

    return problems
