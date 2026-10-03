"""Source hygiene: no plan-phase markers, and no reference to a module that does not exist.

Plan markers (``Phase 0A``, ``T-0A-05``, ``Task E1``, ``v3.1 §3.1``) name the plan a line was
written for, which says nothing to a reader of the code.

Every dotted ``nerdvana_cli.<...>`` path and every ``nerdvana_cli/<...>`` file path in the
source, the tests, the scripts, the documentation and the CI workflows must point at something that exists: a
package, a module, or a name the package defines. A module that moves leaves no re-export
behind, so a path that still names its old place fails here.

Author: 최진호
Date:   2026-10-04
"""

from __future__ import annotations

import ast
import re
import subprocess
from functools import cache
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

MARKER = re.compile(r"Phase [0-9A-Z]|T-0A|Task E1|v3\.1 §")

DOTTED = re.compile(r"(?<![\w.])nerdvana_cli(?:\.\w+)+")
SLASHED = re.compile(r"(?<![\w./-])nerdvana_cli(?:/[\w.-]+)+")

CODE_DIRS = ("nerdvana_cli", "tests", "scripts")
DOC_FILES = ("README.md", "README.ko.md", "NIRNA.md", "CONTRIBUTING.md", "pyproject.toml", "nerdvana.yml.example")


@cache
def _tracked() -> frozenset[Path] | None:
    """Files git tracks, so notes and drafts a developer keeps untracked are not held to the rule; None without git."""
    try:
        listed = subprocess.run(["git", "ls-files", "-z"], cwd=REPO, capture_output=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        return None
    return frozenset(REPO / name for name in listed.decode().split("\0") if name)


def _only_tracked(paths: list[Path]) -> list[Path]:
    tracked = _tracked()
    return paths if tracked is None else [path for path in paths if path in tracked]


def _code_files() -> list[Path]:
    this = Path(__file__).resolve()
    return _only_tracked([path for top in CODE_DIRS for path in sorted((REPO / top).rglob("*.py")) if path.resolve() != this])


def _doc_files() -> list[Path]:
    docs = [path for path in sorted((REPO / "docs").rglob("*.md")) if "plans" not in path.relative_to(REPO / "docs").parts]
    workflows = sorted((REPO / ".github" / "workflows").glob("*.yml"))
    return _only_tracked(docs + workflows + [REPO / name for name in DOC_FILES if (REPO / name).is_file()])


@cache
def _defined_names(init: Path) -> frozenset[str]:
    """Names a package's ``__init__.py`` binds at the top level."""
    if not init.is_file():
        return frozenset()
    names: set[str] = set()
    for node in ast.parse(init.read_text(encoding="utf-8")).body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            names.update(target.id for target in targets if isinstance(target, ast.Name))
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            names.update((alias.asname or alias.name).split(".")[0] for alias in node.names)
    return frozenset(names)


def _dotted_resolves(dotted: str) -> bool:
    current = REPO / "nerdvana_cli"
    for part in dotted.split(".")[1:]:
        if (current / part).is_dir():
            current = current / part
        elif (current / f"{part}.py").is_file():
            return True
        else:
            return part.startswith("__") or part in _defined_names(current / "__init__.py")
    return True


def _slashed_resolves(slashed: str) -> bool:
    path = slashed.rstrip(".")
    return (REPO / path).exists() or (REPO / f"{path}.py").exists()


def _dangling(text: str) -> list[str]:
    found = [match for match in DOTTED.findall(text) if not _dotted_resolves(match)]
    found += [match for match in SLASHED.findall(text) if not _slashed_resolves(match)]
    return found


def test_no_plan_marker_in_the_source_the_tests_or_the_scripts() -> None:
    hits = [
        f"{path.relative_to(REPO)}:{number}"
        for path in _code_files()
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if MARKER.search(line)
    ]
    assert hits == []


def test_every_module_path_points_at_something_that_exists() -> None:
    dangling = [
        f"{path.relative_to(REPO)}: {reference}"
        for path in _code_files() + _doc_files()
        for reference in _dangling(path.read_text(encoding="utf-8"))
    ]
    assert dangling == []


def test_the_scans_recognise_what_they_look_for() -> None:
    assert MARKER.search("# Phase 0A cleanup") and MARKER.search("see T-0A-05") and not MARKER.search("phase of the run")
    assert _dangling("from nerdvana_cli.core.loop.agent_loop import AgentLoop") == []
    assert _dangling("patch('nerdvana_cli.core.agent_loop.AgentLoop')") == ["nerdvana_cli.core.agent_loop.AgentLoop"]
    assert _dangling("see nerdvana_cli/core/tool.py and nerdvana_cli/commands/") == ["nerdvana_cli/commands"]
