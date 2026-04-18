"""Repo Map: compressed symbol dependency graph (Aider-inspired).

Builds a node/edge graph of top-level symbols and their call relationships,
then serialises to a compact JSON string bounded by a token budget.

작성자: 최진호
작성일: 2026-04-18
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from nerdvana_cli.core.symbol import LanguageServerSymbol, Location

# Approximate chars-per-token ratio (conservative: 4 chars ≈ 1 token).
_CHARS_PER_TOKEN = 4


# ---------------------------------------------------------------------------
# Graph nodes / edges
# ---------------------------------------------------------------------------


@dataclass
class SymbolNode:
    """A node in the symbol graph (top-level or depth-1 child)."""

    name_path:  str
    kind:       str
    file_path:  str
    line:       int
    loc_lines:  int = 0           # line count (proxy for size)


@dataclass
class SymbolEdge:
    """A directed edge: *caller* references *callee*."""

    caller: str    # name_path of referencing symbol
    callee: str    # name_path of referenced symbol


@dataclass
class SymbolGraph:
    """In-memory symbol reference graph with compact JSON export.

    Attributes
    ----------
    nodes:
        All symbols collected during graph construction.
    edges:
        Call/reference relationships between symbols.
    """

    nodes: dict[str, SymbolNode] = field(default_factory=dict)
    edges: list[SymbolEdge]      = field(default_factory=list)

    # -- construction helpers --

    def add_symbol(self, sym: LanguageServerSymbol, loc_lines: int = 0) -> None:
        """Add a symbol node (and its depth-1 children) to the graph."""
        node = SymbolNode(
            name_path = sym.name_path,
            kind      = sym.kind,
            file_path = sym.location.file_path,
            line      = sym.location.line,
            loc_lines = loc_lines,
        )
        self.nodes[sym.name_path] = node

        for child in sym.children:
            child_node = SymbolNode(
                name_path = child.name_path,
                kind      = child.kind,
                file_path = child.location.file_path,
                line      = child.location.line,
                loc_lines = 0,
            )
            self.nodes[child.name_path] = child_node

    def add_references(
        self,
        symbol:     LanguageServerSymbol,
        references: list[Location],
    ) -> None:
        """Add edges from each reference location back to *symbol*.

        For each reference location, attempt to find the referencing symbol
        in the graph by file/line proximity, then add a directed edge
        referencing_symbol → symbol.
        """
        for ref in references:
            caller_name = self._find_symbol_at(ref.file_path, ref.line)
            if caller_name and caller_name != symbol.name_path:
                edge = SymbolEdge(caller=caller_name, callee=symbol.name_path)
                if edge not in self.edges:
                    self.edges.append(edge)

    def _find_symbol_at(self, file_path: str, line: int) -> str | None:
        """Return the name_path of the symbol whose definition is nearest
        (and before) the given file/line, or None.
        """
        best_node: SymbolNode | None = None
        for node in self.nodes.values():
            if node.file_path != file_path:
                continue
            if node.line > line:
                continue
            if best_node is None or node.line > best_node.line:
                best_node = node
        return best_node.name_path if best_node else None

    # -- serialisation --

    def to_compact_json(self, max_tokens: int = 4000) -> str:
        """Return a compact JSON string within the token budget.

        Symbols are sorted by LOC descending (most-important first).
        Edges are included while budget permits.
        """
        max_chars = max_tokens * _CHARS_PER_TOKEN

        # Sort nodes: larger symbols first (more context-worthy)
        sorted_nodes = sorted(
            self.nodes.values(),
            key=lambda n: (-n.loc_lines, n.name_path),
        )

        # Build output incrementally
        output: dict[str, Any] = {"symbols": [], "edges": []}
        current_chars = len('{"symbols":[],"edges":[]}')

        for node in sorted_nodes:
            entry: dict[str, Any] = {
                "path": node.name_path,
                "kind": node.kind,
                "file": node.file_path,
                "line": node.line,
            }
            if node.loc_lines:
                entry["loc"] = node.loc_lines
            entry_str = json.dumps(entry, ensure_ascii=False)
            if current_chars + len(entry_str) + 2 > max_chars:
                break
            output["symbols"].append(entry)
            current_chars += len(entry_str) + 2   # comma + space overhead

        for edge in self.edges:
            edge_entry = {"from": edge.caller, "to": edge.callee}
            edge_str   = json.dumps(edge_entry, ensure_ascii=False)
            if current_chars + len(edge_str) + 2 > max_chars:
                break
            output["edges"].append(edge_entry)
            current_chars += len(edge_str) + 2

        return json.dumps(output, ensure_ascii=False, separators=(",", ":"))

    def node_count(self) -> int:
        return len(self.nodes)

    def edge_count(self) -> int:
        return len(self.edges)
