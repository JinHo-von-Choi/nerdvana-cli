"""Package dependency direction inside nerdvana_cli.

Every import is read from the source with ast, including imports deferred into
function bodies, because a deferred import still binds the importing package to
the imported one. Only ``if TYPE_CHECKING:`` blocks are skipped, since they
never execute.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

from tests.contracts.import_graph import parsed_sources, runtime_imports

FORBIDDEN: dict[str, frozenset[str]] = {
    "types":     frozenset({"utils", "core", "providers", "tools", "agents", "mcp", "ui", "server", "cli", "main"}),
    "utils":     frozenset({"core", "providers", "tools", "agents", "mcp", "ui", "server", "cli", "main"}),
    "providers": frozenset({"core", "tools", "ui", "server", "cli", "main"}),
    "codeintel": frozenset({"core", "providers", "tools", "agents", "mcp", "ui", "server", "cli", "main"}),
    "core":      frozenset({"codeintel", "external", "tools", "ui", "server", "cli", "main"}),
    "external":  frozenset({"tools", "ui", "server", "cli", "main"}),
    "tools":     frozenset({"ui", "server", "cli", "main"}),
    "mcp":       frozenset({"tools", "ui", "server", "cli", "main"}),
    "agents":    frozenset({"tools", "ui", "server", "cli", "main"}),
    "server":    frozenset({"ui", "cli", "main"}),
    "ui":        frozenset({"server", "main"}),
    "cli":       frozenset({"main"}),
}

# (source file relative to the package root, target package)
ALLOWED: frozenset[tuple[str, str]] = frozenset()


def _edges() -> list[tuple[str, int, str, str]]:
    """(source file, line, source package, target package) for cross-package imports."""
    edges: list[tuple[str, int, str, str]] = []
    for rel, tree in parsed_sources().items():
        parts = rel.split("/")
        if len(parts) < 2:
            continue
        source = parts[0]
        for line, module in runtime_imports(tree):
            target = module.split(".")[1]
            if target != source:
                edges.append((rel, line, source, target))
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
