"""The fixed part of every request stays small: prompt sections, tool declarations, project documents.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

from nerdvana_cli.core.context.nirnamd import NirnaFile, fit_to_budget
from nerdvana_cli.core.context.prompts import build_system_prompt
from nerdvana_cli.core.context.token_estimator import approx_tokens

SCRIPT = Path(__file__).parent.parent / "scripts" / "measure_prompt_overhead.py"

# Ceilings for the default tool set measured in an empty directory (2026-10-03: about 1,200 and
# 4,700). They are not targets; a change that crosses one has to say why in the diff.
SYSTEM_PROMPT_CEILING = 2_000
DECLARATION_CEILING   = 6_000


def _measure(cwd: Path) -> dict:
    spec   = importlib.util.spec_from_file_location("measure_prompt_overhead", SCRIPT)
    assert spec and spec.loader
    module: ModuleType = importlib.util.module_from_spec(spec)
    sys.modules["measure_prompt_overhead"] = module
    spec.loader.exec_module(module)
    return module.measure(str(cwd))


@pytest.fixture()
def empty_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("NERDVANA_DATA_HOME", str(tmp_path / "data"))
    project = tmp_path / "project"
    project.mkdir()
    return project


def test_the_default_prompt_and_declarations_stay_under_their_ceilings(empty_project: Path) -> None:
    report = _measure(empty_project)
    assert report["system_prompt_tokens"] <= SYSTEM_PROMPT_CEILING
    assert report["tool_declaration_tokens"] <= DECLARATION_CEILING


def test_no_tool_description_is_repeated_in_the_system_prompt(empty_project: Path) -> None:
    from nerdvana_cli.core.agent_loop import AgentLoop
    from nerdvana_cli.core.config.settings import NerdvanaSettings
    from nerdvana_cli.tools.registry import create_tool_registry

    settings     = NerdvanaSettings()
    settings.cwd = str(empty_project)
    registry     = create_tool_registry(settings=settings)
    prompt       = AgentLoop(settings=settings, registry=registry).build_system_prompt()
    for tool in registry.all_tools():
        first_line = tool.description_text.strip().splitlines()[0] if tool.description_text.strip() else ""
        if len(first_line) > 40:
            assert first_line not in prompt, tool.name


def test_the_report_lists_sections_and_declarations(empty_project: Path) -> None:
    report = _measure(empty_project)
    assert report["tools"] > 10
    assert report["sections"][0][0] >= report["sections"][-1][0]
    assert {"FileEdit", "Bash"} <= {name for _, name in report["declarations"]}


# ---------------------------------------------------------------------------
# Project document budget
# ---------------------------------------------------------------------------


def _doc(paragraphs: int) -> NirnaFile:
    return NirnaFile(path="/p/NIRNA.md", type="project", content="\n\n".join(f"Rule {n}: " + "word " * 40 for n in range(paragraphs)))


def test_without_a_budget_documents_are_kept_whole() -> None:
    files = [_doc(30)]
    assert fit_to_budget(files, 0) == files


def test_a_long_document_is_cut_at_a_paragraph_and_names_the_file() -> None:
    (cut,) = fit_to_budget([_doc(30)], 300)
    assert cut.content.count("Rule ") < 30
    assert cut.content.startswith("Rule 0:")
    assert "Read /p/NIRNA.md for the rest" in cut.content
    assert approx_tokens(cut.content) < 500


def test_a_short_document_is_left_alone_and_the_first_paragraph_always_survives() -> None:
    short = _doc(2)
    assert fit_to_budget([short], 5_000) == [short]
    (tiny,) = fit_to_budget([_doc(5)], 1)
    assert tiny.content.startswith("Rule 0:")


def test_the_budget_reaches_the_system_prompt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NERDVANA_DATA_HOME", str(tmp_path / "data"))
    (tmp_path / "NIRNA.md").write_text(_doc(40).content, encoding="utf-8")
    whole = build_system_prompt(tools=[], cwd=str(tmp_path))
    cut   = build_system_prompt(tools=[], cwd=str(tmp_path), project_doc_max_tokens=300)
    assert approx_tokens(cut) < approx_tokens(whole) - 1_000
    assert "Rule 39" in whole and "Rule 39" not in cut
