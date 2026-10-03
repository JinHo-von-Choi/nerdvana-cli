"""Dependency direction between the subpackages of nerdvana_cli.core.

The subpackages and what each owns are described in docs/architecture.md. Four
rules hold, read from every runtime import (deferred ones included):

- ``core.config`` imports nothing else from core;
- ``core.context``, ``core.safety`` and ``core.telemetry`` do not import ``core.loop``;
- only ``core.loop`` and the tools package import ``core.execution``;
- the subpackages, with the modules at the top of core each as their own node,
  form no import cycle. A cycle that exists today would be listed in
  ``KNOWN_CYCLES``, which may only shrink.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

from tests.contracts.import_graph import PREFIX, module_graph
from tests.contracts.test_import_cycles import _strongly_connected

CORE = f"{PREFIX}.core"

SUBPACKAGES: frozenset[str] = frozenset(
    {"config", "telemetry", "hooks", "safety", "context", "state", "delegation", "execution", "loop"},
)

NO_LOOP: frozenset[str] = frozenset({"context", "safety", "telemetry"})

KNOWN_CYCLES: frozenset[frozenset[str]] = frozenset()


def _node(module: str) -> str | None:
    """The subpackage a core module belongs to, ``core.<name>`` for a top-level module, None outside core."""
    if not module.startswith(f"{CORE}."):
        return None
    name = module.split(".")[2]
    return name if name in SUBPACKAGES else f"core.{name}"


def _edges() -> list[tuple[str, str, str, str]]:
    """(importing module, imported module, importing node, imported node) for imports into core."""
    found: list[tuple[str, str, str, str]] = []
    for source, targets in module_graph().items():
        for target in targets:
            target_node = _node(target)
            if target_node is not None:
                found.append((source, target, _node(source) or source.split(".")[1], target_node))
    return found


def test_config_imports_nothing_else_from_core() -> None:
    stray = [
        f"{source} -> {target}"
        for source, target, source_node, target_node in _edges()
        if source_node == "config" and target_node != "config"
    ]
    assert stray == []


def test_context_safety_and_telemetry_do_not_import_the_loop() -> None:
    stray = [
        f"{source} -> {target}"
        for source, target, source_node, target_node in _edges()
        if source_node in NO_LOOP and target_node == "loop"
    ]
    assert stray == []


def test_only_the_loop_and_the_tools_import_execution() -> None:
    stray = [
        f"{source} -> {target}"
        for source, target, source_node, target_node in _edges()
        if target_node == "execution" and source_node not in {"execution", "loop", "tools"}
    ]
    assert stray == []


def _subpackage_graph() -> dict[str, frozenset[str]]:
    graph: dict[str, set[str]] = {}
    for source, _target, source_node, target_node in _edges():
        if _node(source) is not None and source_node != target_node:
            graph.setdefault(source_node, set()).add(target_node)
    return {node: frozenset(targets) for node, targets in graph.items()}


def test_no_cycle_among_the_subpackages_outside_the_known_list() -> None:
    new = [
        ", ".join(sorted(cycle))
        for cycle in _strongly_connected(_subpackage_graph())
        if not any(cycle <= known for known in KNOWN_CYCLES)
    ]
    assert new == []


def test_every_known_cycle_still_exists() -> None:
    current = set(_strongly_connected(_subpackage_graph()))
    assert sorted(", ".join(sorted(known)) for known in KNOWN_CYCLES if known not in current) == []


def test_every_subpackage_exists_and_only_the_tool_module_sits_at_the_top() -> None:
    modules = {module for module in module_graph() if module.startswith(f"{CORE}.")}
    present = {module.split(".")[2] for module in modules}
    assert present >= SUBPACKAGES
    assert present - SUBPACKAGES == {"tool"}
