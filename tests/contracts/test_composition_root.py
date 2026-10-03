"""The agent loop and the tool registry are built in one place.

``cli/bootstrap.py`` is the composition root; ``core/subagent.py`` builds the
isolated loop each sub-agent runs in. A call of ``AgentLoop(`` or
``create_tool_registry(`` anywhere else in the package must be listed in
``ALLOWED``, which may only shrink.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

import ast

from tests.contracts.import_graph import parsed_sources

BUILDERS: frozenset[str] = frozenset({"AgentLoop", "create_tool_registry"})
ROOTS:    frozenset[str] = frozenset({"cli/bootstrap.py", "core/subagent.py"})

# (source file relative to the package root, builder name)
ALLOWED: frozenset[tuple[str, str]] = frozenset()


def _calls() -> list[tuple[str, int, str]]:
    """(source file, line, builder) for every call of a builder in the package."""
    found: list[tuple[str, int, str]] = []
    for rel, tree in parsed_sources().items():
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else ""
            if name in BUILDERS:
                found.append((rel, node.lineno, name))
    return found


def test_the_loop_and_the_registry_are_built_only_in_the_composition_root() -> None:
    stray = [f"{rel}:{line} {name}(" for rel, line, name in _calls() if rel not in ROOTS and (rel, name) not in ALLOWED]
    assert stray == []


def test_every_allowed_exception_is_still_in_use() -> None:
    present = {(rel, name) for rel, _line, name in _calls()}
    assert sorted(ALLOWED - present) == []


def test_the_composition_root_builds_both() -> None:
    built = {name for rel, _line, name in _calls() if rel == "cli/bootstrap.py"}
    assert built == BUILDERS
