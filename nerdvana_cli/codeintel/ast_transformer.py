"""Deterministic AST-based codemod pass: symbol, keyword, and import rewrites.

Author: 최진호
Date: 2026-10-05
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field


@dataclass
class RewriteRule:
    """One deterministic rewrite: a call symbol, an import path, and keyword renames."""

    old_symbol: str
    new_symbol: str
    old_import: str = ""
    new_import: str = ""
    rename_args: dict[str, str] = field(default_factory=dict)


def _dotted_name(node: ast.AST) -> str | None:
    """The dotted path of *node* (``client.fetch``), or None when it is not a plain path."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _dotted_name(node.value)
        return f"{base}.{node.attr}" if base else None
    return None


def _dotted_node(name: str) -> ast.expr:
    """Build the Name/Attribute chain that calls the dotted path *name*."""
    parts = name.split(".")
    node: ast.expr = ast.Name(id=parts[0], ctx=ast.Load())
    for part in parts[1:]:
        node = ast.Attribute(value=node, attr=part, ctx=ast.Load())
    return node


class AstTransformer(ast.NodeTransformer):
    """Apply RewriteRule instances to a parsed module, counting every change."""

    def __init__(self, rules: list[RewriteRule]) -> None:
        self.rules = rules
        self.transform_count = 0

    def visit_Call(self, node: ast.Call) -> ast.Call:
        self.generic_visit(node)
        name = _dotted_name(node.func)
        matched = next(
            (r for r in self.rules if r.old_symbol and name == r.old_symbol),
            None,
        )
        if matched is None:
            return node
        node.func = _dotted_node(matched.new_symbol)
        self.transform_count += 1
        for keyword in node.keywords:
            if keyword.arg is not None and keyword.arg in matched.rename_args:
                keyword.arg = matched.rename_args[keyword.arg]
                self.transform_count += 1
        return node

    def visit_Import(self, node: ast.Import) -> ast.Import:
        for alias in node.names:
            for rule in self.rules:
                if rule.old_import and alias.name == rule.old_import:
                    alias.name = rule.new_import
                    self.transform_count += 1
                    break
        return node

    def visit_ImportFrom(self, node: ast.ImportFrom) -> ast.ImportFrom:
        if node.module:
            for rule in self.rules:
                if rule.old_import and node.module == rule.old_import:
                    node.module = rule.new_import
                    self.transform_count += 1
                    break
        return node


def transform_source(source: str, rules: list[RewriteRule]) -> tuple[str, int]:
    """Rewrite *source* with *rules* and return (new_source, total_transforms).

    The original source string is returned untouched when no rule matched.
    """
    tree = ast.parse(source)
    transformer = AstTransformer(rules)
    transformer.visit(tree)
    if transformer.transform_count == 0:
        return source, 0
    ast.fix_missing_locations(tree)
    return ast.unparse(tree), transformer.transform_count
