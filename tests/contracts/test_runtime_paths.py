"""Data root resolution lives in core/config/paths.py and core/config/settings.py only.

Two constructions are tracked outside those two modules: building the user data
root as ``Path.home() / ".nerdvana"`` and naming one of the NERDVANA_DATA_HOME,
NERDVANA_HOME or NERDVANA_CONFIG environment variables. Both bypass
``paths.user_data_home()`` and ``paths.install_root()``, so a user who sets the
variable gets a different root for those files. Other user home access, such as
``~/.claude/skills`` or ``~/.codex``, is not a violation.

Sites that exist today are listed in ``ALLOWED`` as (file, kind). The list may
only shrink, and an entry whose construction is gone must be removed.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

import ast

from tests.contracts.import_graph import parsed_sources

OWNERS = frozenset({"core/config/paths.py", "core/config/settings.py"})

HOME_ROOT = "home-data-root"
ENV_READ  = "env-read"

ENV_NAMES = frozenset({"NERDVANA_DATA_HOME", "NERDVANA_HOME", "NERDVANA_CONFIG"})

# (source file relative to the package root, kind)
ALLOWED: frozenset[tuple[str, str]] = frozenset()


def _is_home_call(node: ast.AST) -> bool:
    return isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "home"


def _is_data_root_join(node: ast.BinOp) -> bool:
    """``<home call> / ".nerdvana"`` at the head of a ``/`` chain."""
    if not isinstance(node.op, ast.Div):
        return False
    head = node
    while isinstance(head.left, ast.BinOp) and isinstance(head.left.op, ast.Div):
        head = head.left
    return (
        _is_home_call(head.left)
        and isinstance(head.right, ast.Constant)
        and head.right.value == ".nerdvana"
    )


def _kinds(tree: ast.AST) -> dict[str, int]:
    """Kind of each violating construction in a tree, with the line of its first occurrence."""
    found: dict[str, int] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.BinOp) and _is_data_root_join(node):
            found.setdefault(HOME_ROOT, node.lineno)
        elif isinstance(node, ast.Constant) and node.value in ENV_NAMES:
            found.setdefault(ENV_READ, node.lineno)
    return found


def _sites() -> dict[tuple[str, str], int]:
    return {
        (rel, kind): line
        for rel, tree in parsed_sources().items()
        if rel not in OWNERS
        for kind, line in _kinds(tree).items()
    }


def test_no_code_outside_the_path_owners_resolves_the_data_root_itself() -> None:
    violations = [
        f"{rel}:{line} {kind}"
        for (rel, kind), line in sorted(_sites().items())
        if (rel, kind) not in ALLOWED
    ]
    assert violations == []


def test_every_allowed_site_still_exists() -> None:
    assert sorted(ALLOWED - set(_sites())) == []


def test_the_scan_recognises_the_constructions_and_ignores_other_home_access() -> None:
    data_root = ast.parse('root = Path.home() / ".nerdvana" / "audit.sqlite"')
    env_read  = ast.parse('value = os.environ.get("NERDVANA_DATA_HOME", "")')
    other     = ast.parse('skills = Path.home() / ".claude" / "skills"\nproject = cwd / ".nerdvana"')
    assert list(_kinds(data_root)) == [HOME_ROOT]
    assert list(_kinds(env_read))  == [ENV_READ]
    assert _kinds(other) == {}
