"""Package dependency direction inside nerdvana_cli.

Every import is read from the source with ast, including imports deferred into
function bodies, because a deferred import still binds the importing package to
the imported one. Only ``if TYPE_CHECKING:`` blocks are skipped, since they
never execute.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

import ast
from pathlib import Path

import nerdvana_cli

ROOT = Path(nerdvana_cli.__file__).resolve().parent

FORBIDDEN: dict[str, frozenset[str]] = {
    "types":     frozenset({"utils", "core", "providers", "tools", "agents", "mcp", "ui", "server", "commands", "main"}),
    "utils":     frozenset({"core", "providers", "tools", "agents", "mcp", "ui", "server", "commands", "main"}),
    "providers": frozenset({"core", "tools", "ui", "server", "commands", "main"}),
    "core":      frozenset({"tools", "ui", "server", "commands", "main"}),
    "tools":     frozenset({"ui", "server", "commands", "main"}),
}

# (source file relative to the package root, target package)
ALLOWED: frozenset[tuple[str, str]] = frozenset({
    ("core/agent_loop.py", "tools"),
})


def _is_type_checking_guard(node: ast.If) -> bool:
    test = node.test
    if isinstance(test, ast.Name):
        return test.id == "TYPE_CHECKING"
    return isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"


def _runtime_imports(tree: ast.AST) -> list[tuple[int, str]]:
    """(line, module) of every nerdvana_cli import outside TYPE_CHECKING blocks."""
    found: list[tuple[int, str]] = []

    def visit(node: ast.AST) -> None:
        if isinstance(node, ast.If) and _is_type_checking_guard(node):
            for child in node.orelse:
                visit(child)
            return
        if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            found.append((node.lineno, node.module))
        elif isinstance(node, ast.Import):
            found.extend((node.lineno, alias.name) for alias in node.names)
        for child in ast.iter_child_nodes(node):
            visit(child)

    visit(tree)
    return [(line, module) for line, module in found if module.startswith("nerdvana_cli.")]


def _edges() -> list[tuple[str, int, str, str]]:
    """(source file, line, source package, target package) for cross-package imports."""
    edges: list[tuple[str, int, str, str]] = []
    for path in sorted(ROOT.rglob("*.py")):
        rel = path.relative_to(ROOT)
        if len(rel.parts) < 2:
            continue
        source = rel.parts[0]
        tree   = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for line, module in _runtime_imports(tree):
            target = module.split(".")[1]
            if target != source:
                edges.append((rel.as_posix(), line, source, target))
    return edges


def test_no_package_imports_against_the_layer_direction() -> None:
    violations = [
        f"{file}:{line} {source} -> {target}"
        for file, line, source, target in _edges()
        if target in FORBIDDEN.get(source, frozenset()) and (file, target) not in ALLOWED
    ]
    assert violations == []


def test_every_allowed_exception_is_still_in_use() -> None:
    present = {(file, target) for file, _line, _source, target in _edges()}
    assert sorted(ALLOWED - present) == []
