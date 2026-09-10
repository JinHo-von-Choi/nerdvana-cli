"""The documentation set must describe the code as it currently behaves.

`scripts/check_docs_consistency.py` holds the comparisons; this module runs
them under pytest so a drifting document fails the gate instead of waiting
for a reader to notice.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "check_docs_consistency.py"


def _load_checker() -> ModuleType:
    """Import the checker script by path; scripts/ is not an importable package."""
    spec = importlib.util.spec_from_file_location("check_docs_consistency", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


checker = _load_checker()


def test_every_documentation_claim_matches_the_code() -> None:
    report = checker.run_checks()
    detail = "\n".join(f"{doc}: {message}" for doc, message in report.problems)
    assert not report.problems, f"documentation drifted from the code:\n{detail}"


def test_checker_covers_the_documents_it_claims_to() -> None:
    for name in checker.DOC_FILES:
        assert (REPO_ROOT / name).is_file(), f"checked document is missing: {name}"


def test_tool_roster_is_read_from_the_registry() -> None:
    """A guard on the guard: an empty roster would make the tool check vacuous."""
    names = checker.code_tool_names()
    assert {"Bash", "FileRead", "TodoWrite", "WebSearch"} <= names


def test_command_tree_is_read_from_typer() -> None:
    tree = checker.code_command_tree()
    assert {"run", "serve", "doctor", "cost"} <= tree[""]
    assert "list" in tree["session"]
