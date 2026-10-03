"""Runtime import cycle ratchet for nerdvana_cli.

The module graph is built from every runtime import, deferred ones included,
and split into strongly connected components with Tarjan's algorithm. A
component of two or more modules is an import cycle. Each cycle that exists
today is listed in ``KNOWN_CYCLES``. A cycle that is not listed fails the test,
a listed cycle may lose members but never gain any, and a listed cycle that no
longer exists in exactly that form must be removed or shrunk in the list.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

from tests.contracts.import_graph import module_graph

KNOWN_CYCLES: frozenset[frozenset[str]] = frozenset({
    frozenset({
        "nerdvana_cli.core.agent_loop",
        "nerdvana_cli.core.subagent",
        "nerdvana_cli.core.swarm",
        "nerdvana_cli.tools.agent_tool",
        "nerdvana_cli.tools.registry",
        "nerdvana_cli.tools.swarm_tool",
    }),
})


def _strongly_connected(graph: dict[str, frozenset[str]]) -> list[frozenset[str]]:
    """Components of two or more modules, by Tarjan's algorithm in O(V + E)."""
    order:    dict[str, int]       = {}
    lowlink:  dict[str, int]       = {}
    on_stack: set[str]             = set()
    stack:    list[str]            = []
    found:    list[frozenset[str]] = []

    def visit(node: str) -> None:
        order[node] = lowlink[node] = len(order)
        stack.append(node)
        on_stack.add(node)
        for target in sorted(graph.get(node, frozenset())):
            if target not in order:
                visit(target)
                lowlink[node] = min(lowlink[node], lowlink[target])
            elif target in on_stack:
                lowlink[node] = min(lowlink[node], order[target])
        if lowlink[node] != order[node]:
            return
        members: set[str] = set()
        while True:
            member = stack.pop()
            on_stack.discard(member)
            members.add(member)
            if member == node:
                break
        if len(members) > 1:
            found.append(frozenset(members))

    for node in sorted(graph):
        if node not in order:
            visit(node)
    return found


def _describe(cycle: frozenset[str]) -> str:
    return ", ".join(sorted(cycle))


def test_no_import_cycle_outside_the_known_list_and_known_ones_do_not_grow() -> None:
    new = [
        _describe(cycle)
        for cycle in _strongly_connected(module_graph())
        if not any(cycle <= known for known in KNOWN_CYCLES)
    ]
    assert new == []


def test_every_known_cycle_still_exists_in_exactly_that_form() -> None:
    current = set(_strongly_connected(module_graph()))
    stale   = [_describe(known) for known in KNOWN_CYCLES if known not in current]
    assert stale == []


def test_tarjan_finds_a_cycle_and_ignores_acyclic_edges() -> None:
    graph = {
        "a": frozenset({"b"}),
        "b": frozenset({"c"}),
        "c": frozenset({"a", "d"}),
        "d": frozenset(),
    }
    assert _strongly_connected(graph) == [frozenset({"a", "b", "c"})]
