"""Unit tests for nerdvana_cli.core.symbol_graph.

Tests SymbolGraph construction, edge management, and compact JSON export.
No LSP process required.

작성자: 최진호
작성일: 2026-04-18
"""
from __future__ import annotations

import json

import pytest

from nerdvana_cli.core.symbol import (
    LanguageServerSymbol,
    Location,
    _sym_from_dict,
)
from nerdvana_cli.core.symbol_graph import SymbolEdge, SymbolGraph, SymbolNode


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_sym(
    name:      str,
    file_path: str = "/proj/foo.py",
    line:      int = 1,
    kind:      str = "Function",
    kind_int:  int = 12,
    children:  list[LanguageServerSymbol] | None = None,
) -> LanguageServerSymbol:
    return LanguageServerSymbol(
        name      = name,
        name_path = name,
        kind      = kind,
        kind_int  = kind_int,
        location  = Location(file_path=file_path, line=line, character=0),
        children  = children or [],
    )


def _make_child(
    name:       str,
    parent_path: str,
    file_path: str = "/proj/foo.py",
    line:      int = 5,
) -> LanguageServerSymbol:
    return LanguageServerSymbol(
        name      = name,
        name_path = f"{parent_path}/{name}",
        kind      = "Method",
        kind_int  = 6,
        location  = Location(file_path=file_path, line=line, character=4),
        children  = [],
    )


# ---------------------------------------------------------------------------
# Test: node construction
# ---------------------------------------------------------------------------


class TestSymbolGraph:
    def test_add_symbol_top_level_only(self) -> None:
        graph = SymbolGraph()
        sym   = _make_sym("my_func")
        graph.add_symbol(sym)
        assert "my_func" in graph.nodes
        assert graph.node_count() == 1

    def test_add_symbol_with_children(self) -> None:
        graph   = SymbolGraph()
        child   = _make_child("helper", "MyClass")
        parent  = _make_sym("MyClass", kind="Class", kind_int=5, children=[child])
        graph.add_symbol(parent)
        assert "MyClass" in graph.nodes
        assert "MyClass/helper" in graph.nodes
        assert graph.node_count() == 2

    def test_add_references_creates_edges(self) -> None:
        graph  = SymbolGraph()
        caller = _make_sym("caller_func", line=1)
        callee = _make_sym("callee_func", line=10)
        graph.add_symbol(caller)
        graph.add_symbol(callee)

        ref_loc = Location(file_path="/proj/foo.py", line=3, character=0)
        graph.add_references(callee, [ref_loc])

        assert graph.edge_count() == 1
        edge = graph.edges[0]
        assert edge.caller == "caller_func"
        assert edge.callee == "callee_func"

    def test_duplicate_edges_not_added(self) -> None:
        graph  = SymbolGraph()
        caller = _make_sym("A", line=1)
        callee = _make_sym("B", line=20)
        graph.add_symbol(caller)
        graph.add_symbol(callee)

        ref = Location(file_path="/proj/foo.py", line=5, character=0)
        graph.add_references(callee, [ref, ref])   # same ref twice
        assert graph.edge_count() == 1


# ---------------------------------------------------------------------------
# Test: compact JSON export
# ---------------------------------------------------------------------------


class TestSymbolGraphCompactJson:
    def test_compact_json_valid(self) -> None:
        graph = SymbolGraph()
        for i in range(3):
            graph.add_symbol(_make_sym(f"func_{i}", line=i * 10 + 1))
        j = graph.to_compact_json()
        data = json.loads(j)
        assert "symbols" in data
        assert "edges" in data
        assert len(data["symbols"]) == 3

    def test_token_budget_truncates_symbols(self) -> None:
        graph = SymbolGraph()
        # Add many symbols
        for i in range(200):
            graph.add_symbol(_make_sym(f"func_{i:04d}", line=i + 1))

        # With a very small budget, only a few symbols fit
        j    = graph.to_compact_json(max_tokens=50)
        data = json.loads(j)
        assert len(data["symbols"]) < 200
        assert len(j) <= 50 * 4 + 200   # rough upper bound

    def test_loc_lines_influences_ordering(self) -> None:
        graph = SymbolGraph()
        sym_small = _make_sym("small_func", line=1)
        sym_large = _make_sym("large_func", line=20)
        graph.add_symbol(sym_small, loc_lines=5)
        graph.add_symbol(sym_large, loc_lines=100)

        j    = graph.to_compact_json()
        data = json.loads(j)
        names = [s["path"] for s in data["symbols"]]
        # large_func should appear first (higher LOC = more important)
        assert names.index("large_func") < names.index("small_func")

    def test_empty_graph(self) -> None:
        graph = SymbolGraph()
        j    = graph.to_compact_json()
        data = json.loads(j)
        assert data["symbols"] == []
        assert data["edges"] == []
