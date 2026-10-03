"""The test command a project can be checked with when nobody named one.

Author: 최진호
Date:   2026-10-03

``goal.auto_verify`` runs this command before a run that changed files is accepted as finished. The
detection is conservative: it looks only at the files that name the ecosystem, and says nothing when
the project does not clearly have tests or the tool that runs them is not installed, so that a project
it does not understand is never held to a command that cannot pass.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

# npm's placeholder for a package that has no tests.
_NPM_PLACEHOLDER = "no test specified"


def _has_python_tests(root: Path) -> bool:
    if (root / "pyproject.toml").is_file() or (root / "pytest.ini").is_file():
        return True
    tests = root / "tests"
    return tests.is_dir() and any(tests.rglob("test_*.py"))


def _has_npm_test_script(root: Path) -> bool:
    try:
        scripts = json.loads((root / "package.json").read_text(encoding="utf-8")).get("scripts")
    except (OSError, ValueError, AttributeError):
        return False
    script = scripts.get("test") if isinstance(scripts, dict) else None
    return isinstance(script, str) and bool(script.strip()) and _NPM_PLACEHOLDER not in script


def detect_test_command(cwd: str) -> str:
    """The command that runs the tests of the project in *cwd*, or an empty string when none is clear.

    Checked in this order: pytest (a ``pyproject.toml``, a ``pytest.ini`` or Python tests under
    ``tests/``), ``npm test`` (a ``test`` script in ``package.json``), ``cargo test`` (``Cargo.toml``)
    and ``go test ./...`` (``go.mod``). A candidate whose program is not on the PATH is skipped.
    """
    root = Path(cwd)
    candidates = (
        ("pytest", "pytest -q",       _has_python_tests(root)),
        ("npm",    "npm test",        _has_npm_test_script(root)),
        ("cargo",  "cargo test",      (root / "Cargo.toml").is_file()),
        ("go",     "go test ./...",   (root / "go.mod").is_file()),
    )
    for program, command, present in candidates:
        if present and shutil.which(program):
            return command
    return ""
