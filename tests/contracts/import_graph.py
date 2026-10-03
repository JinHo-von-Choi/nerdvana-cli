"""Shared AST readers for the structure contract tests.

Every import is read from the source with ast, including imports deferred into
function bodies, because a deferred import still binds the importing module to
the imported one. Only ``if TYPE_CHECKING:`` blocks are skipped, since they
never execute.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from functools import cache
from pathlib import Path

import nerdvana_cli

ROOT   = Path(nerdvana_cli.__file__).resolve().parent
PREFIX = "nerdvana_cli"


def is_type_checking_guard(node: ast.If) -> bool:
    """True for ``if TYPE_CHECKING:`` and ``if typing.TYPE_CHECKING:``."""
    test = node.test
    if isinstance(test, ast.Name):
        return test.id == "TYPE_CHECKING"
    return isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"


def _executed_imports(node: ast.AST) -> Iterator[ast.Import | ast.ImportFrom]:
    if isinstance(node, ast.If) and is_type_checking_guard(node):
        for child in node.orelse:
            yield from _executed_imports(child)
        return
    if isinstance(node, (ast.Import, ast.ImportFrom)):
        yield node
    for child in ast.iter_child_nodes(node):
        yield from _executed_imports(child)


def runtime_imports(tree: ast.AST) -> list[tuple[int, str]]:
    """(line, module) of every nerdvana_cli import outside TYPE_CHECKING blocks."""
    found: list[tuple[int, str]] = []
    for node in _executed_imports(tree):
        if isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                found.append((node.lineno, node.module))
        else:
            found.extend((node.lineno, alias.name) for alias in node.names)
    return [(line, module) for line, module in found if module.startswith(f"{PREFIX}.")]


def runtime_import_targets(tree: ast.AST, known_modules: frozenset[str]) -> list[tuple[int, str]]:
    """(line, module) of every runtime import, resolved to the module that is bound.

    ``from nerdvana_cli.core.loop import agent_loop`` binds the submodule
    ``nerdvana_cli.core.loop.agent_loop``; when the imported name is not a module the
    import binds the package (or module) named by the ``from`` clause.
    """
    found: list[tuple[int, str]] = []
    for node in _executed_imports(tree):
        if isinstance(node, ast.Import):
            found.extend((node.lineno, alias.name) for alias in node.names if alias.name in known_modules)
        elif node.level == 0 and node.module:
            for alias in node.names:
                submodule = f"{node.module}.{alias.name}"
                target    = submodule if submodule in known_modules else node.module
                if target in known_modules:
                    found.append((node.lineno, target))
    return found


@cache
def parsed_sources() -> dict[str, ast.Module]:
    """Parsed syntax tree of every module, keyed by path relative to the package root."""
    return {
        path.relative_to(ROOT).as_posix(): ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for path in sorted(ROOT.rglob("*.py"))
    }


def module_name(relative_path: str) -> str:
    """Dotted module name of a path relative to the package root."""
    parts = relative_path.removesuffix(".py").split("/")
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join([PREFIX, *parts])


@cache
def module_graph() -> dict[str, frozenset[str]]:
    """Runtime import edges between nerdvana_cli modules, self edges excluded."""
    sources = parsed_sources()
    names   = {rel: module_name(rel) for rel in sources}
    known   = frozenset(names.values())
    return {
        names[rel]: frozenset(
            target for _line, target in runtime_import_targets(tree, known) if target != names[rel]
        )
        for rel, tree in sources.items()
    }
