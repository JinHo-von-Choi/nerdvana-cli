"""A workflow file is validated before anything runs, and its templates, JSON answers and discovery behave as documented.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from nerdvana_cli.core.config.settings import NerdvanaSettings, SettingsLoadError
from nerdvana_cli.core.workflow import (
    ORIGIN_BUNDLED,
    ORIGIN_PROJECT,
    ORIGIN_USER,
    WorkflowError,
    bundled_dir,
    discover,
    load_workflow,
    parse_workflow,
    resolve_inputs,
)
from nerdvana_cli.core.workflow_text import Scope, extract_json, items_of, render, resolve, validate_schema


def _parse(text: str) -> Any:
    return parse_workflow(yaml.safe_load(text))


MINIMAL = """
name: demo
inputs:
  topic: {default: cats, description: what to study}
  depth: 2
  strict: {description: no default means required}
steps:
  - id: first
    prompt: Study ${inputs.topic}.
  - id: second
    prompt: Continue from ${steps.first.output}.
    needs: []
"""


def test_a_workflow_is_read_with_inputs_and_dependencies_taken_from_references() -> None:
    workflow = _parse(MINIMAL)
    assert [step.id for step in workflow.steps] == ["first", "second"]
    assert workflow.steps[1].needs == ("first",)          # derived from ${steps.first.output}
    assert workflow.result == "second"                    # the last step unless result: says otherwise
    inputs = {spec.name: spec for spec in workflow.inputs}
    assert inputs["topic"].default == "cats" and not inputs["topic"].required
    assert inputs["depth"].default == 2
    assert inputs["strict"].required


def test_inputs_are_resolved_over_defaults_and_reject_unknown_and_missing_ones() -> None:
    workflow = _parse(MINIMAL)
    assert resolve_inputs(workflow, {"strict": "x", "topic": "dogs"}) == {"topic": "dogs", "depth": 2, "strict": "x"}
    with pytest.raises(WorkflowError, match="strict is required"):
        resolve_inputs(workflow, {})
    with pytest.raises(WorkflowError, match="unknown input"):
        resolve_inputs(workflow, {"strict": "x", "nope": "1"})


@pytest.mark.parametrize(("text", "message"), [
    ("name: x\nsteps: []\n", "non-empty list"),
    ("name: x\nsteps:\n  - id: a\n    prompt: p\n  - id: a\n    prompt: q\n", "appears twice"),
    ("name: x\nsteps:\n  - id: a\n    prompt: ${steps.b.output}\n  - id: b\n    prompt: ${steps.a.output}\n", "cycle"),
    ("name: x\nsteps:\n  - id: a\n    prompt: ${steps.zzz.output}\n", "steps.<step id>"),
    ("name: x\nsteps:\n  - id: a\n    prompt: ${inputs.nothing}\n", "unknown input"),
    ("name: x\nsteps:\n  - id: a\n    prompt: ${item}\n", "only available in a step with foreach"),
    ("name: x\nsteps:\n  - id: a\n    needs: [a]\n    prompt: p\n", "not another step"),
    ("name: x\nsteps:\n  - id: a\n    prompt: p\n    colour: red\n", "does not apply"),
    ("name: x\nsteps:\n  - id: a\n    kind: verify\n    command: ls\n    prompt: p\n", "does not apply to a verify"),
    ("name: x\nsteps:\n  - id: a\n    prompt: p\n    output: json\n", "needs a schema"),
    ("name: x\nsteps:\n  - id: a\n    prompt: p\n    schema: {type: object}\n", "schema needs output: json"),
    ("name: x\nsteps:\n  - id: a\n    prompt: p\n    foreach: not a reference\n", "foreach must be one reference"),
    ("name: x\nsteps:\n  - id: a\n    kind: cross_check\n    claims: ${steps.b.items}\n    reviewers: 0\n", "reviewers must be between"),
    ("name: x\nsteps:\n  - id: ../a\n    prompt: p\n", "letters, digits"),
    ("name: x\nresult: nope\nsteps:\n  - id: a\n    prompt: p\n", "not a step"),
])
def test_a_definition_that_cannot_run_is_rejected_with_the_reason(text: str, message: str) -> None:
    with pytest.raises(WorkflowError, match=message):
        _parse(text)


def test_a_write_scope_needs_allow_write_in_the_file() -> None:
    step = "  - id: edit\n    prompt: p\n    write_scope: project\n"
    with pytest.raises(WorkflowError, match="allow_write"):
        _parse(f"name: x\nsteps:\n{step}")
    workflow = _parse(f"name: x\nallow_write: true\nsteps:\n{step}")
    assert workflow.steps[0].write_scope == "project"
    assert _parse("name: x\nsteps:\n  - id: a\n    prompt: p\n").steps[0].write_scope == "none"


def test_a_step_may_name_paths_as_its_write_scope_only_with_allow_write() -> None:
    workflow = _parse("name: x\nallow_write: true\nsteps:\n  - id: a\n    prompt: p\n    write_scope: [docs, notes.md]\n")
    assert workflow.steps[0].write_scope == ("docs", "notes.md")


def test_the_bundled_review_workflow_is_valid_and_shaped_as_documented() -> None:
    workflow = load_workflow(bundled_dir() / "review-fanout.yml", ORIGIN_BUNDLED)
    kinds    = {step.id: step.kind for step in workflow.steps}
    assert kinds == {"files": "verify", "review": "agent", "confirm": "cross_check", "summary": "agent"}
    review = next(step for step in workflow.steps if step.id == "review")
    assert review.agent == "code-reviewer" and review.foreach == "${steps.files.items}" and review.write_scope == "none"
    assert not workflow.allow_write


def test_discovery_prefers_the_project_over_the_user_over_the_bundled_and_reports_broken_files(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setenv("NERDVANA_DATA_HOME", str(tmp_path / "data"))
    user    = tmp_path / "data" / "workflows"
    project = tmp_path / "project" / ".nerdvana" / "workflows"
    user.mkdir(parents=True)
    project.mkdir(parents=True)
    body = "name: {name}\ndescription: {origin}\nsteps:\n  - id: a\n    prompt: p\n"
    (user / "review-fanout.yml").write_text(body.format(name="review-fanout", origin="user copy"), encoding="utf-8")
    (user / "mine.yml").write_text(body.format(name="mine", origin="user"), encoding="utf-8")
    (project / "mine.yaml").write_text(body.format(name="mine", origin="project"), encoding="utf-8")
    (project / "broken.yml").write_text("name: broken\nsteps: nope\n", encoding="utf-8")
    found, problems = discover(str(tmp_path / "project"))
    assert found["review-fanout"].origin == ORIGIN_USER
    assert found["mine"].origin == ORIGIN_PROJECT and found["mine"].description == "project"
    assert len(problems) == 1 and "broken.yml" in problems[0]


# ---------------------------------------------------------------------------
# Templates, JSON answers and schemas
# ---------------------------------------------------------------------------


def _scope(**extra: Any) -> Scope:
    steps = {"files": {"output": "a.py\nb.py", "items": ["a.py", "b.py"]}}
    return Scope({"base": "main", "n": 3}, steps, **extra)


def test_references_resolve_inputs_step_results_and_the_current_element() -> None:
    scope = _scope(item={"path": "a.py", "tags": ["x", "y"]}, index=4, in_foreach=True)
    assert render("${inputs.base}/${inputs.n}", scope) == "main/3"
    assert render("${steps.files.output}", scope) == "a.py\nb.py"
    assert render("${steps.files.items}", scope) == '["a.py", "b.py"]'
    assert render("${item.path} #${item_index} ${item.tags.1}", scope) == "a.py #4 y"
    assert resolve("steps.files.items", scope) == ["a.py", "b.py"]


def test_a_reference_to_nothing_is_an_error_not_an_empty_string() -> None:
    with pytest.raises(WorkflowError, match="does not exist"):
        render("${inputs.missing}", _scope())
    with pytest.raises(WorkflowError, match="does not exist"):
        render("${item}", _scope())
    with pytest.raises(WorkflowError, match="does not exist"):
        render("${item.path}", _scope(item={"other": 1}, in_foreach=True))


def test_values_placed_in_a_shell_command_are_quoted() -> None:
    scope = Scope({"base": "main; rm -rf /", "plain": "HEAD"}, {})
    assert render("git diff ${inputs.plain}", scope, shell=True) == "git diff HEAD"
    assert render("git diff ${inputs.base}", scope, shell=True) == "git diff 'main; rm -rf /'"


def test_the_json_in_an_agent_answer_is_found_in_a_fence_or_in_prose() -> None:
    assert extract_json('{"items": [1]}') == {"items": [1]}
    assert extract_json('Here you go:\n```json\n{"items": [1, 2]}\n```\nDone.') == {"items": [1, 2]}
    assert extract_json('The result is [3, 4] as asked.') == [3, 4]
    with pytest.raises(ValueError, match="no JSON"):
        extract_json("nothing structured here")


def test_a_schema_checks_types_required_keys_enums_and_nested_items() -> None:
    schema = {
        "type": "object", "required": ["items"],
        "properties": {"items": {"type": "array", "minItems": 1, "items": {
            "type": "object", "required": ["file"], "properties": {"file": {"type": "string"}, "kind": {"enum": ["bug", "style"]}},
        }}},
    }
    assert validate_schema({"items": [{"file": "a.py", "kind": "bug"}]}, schema) == []
    problems = validate_schema({"items": [{"kind": "other"}, {"file": 3}]}, schema)
    assert any("items[0].file is required" in problem for problem in problems)
    assert any("items[0].kind must be one of" in problem for problem in problems)
    assert any("items[1].file must be string" in problem for problem in problems)
    assert validate_schema({}, schema) == ["$.items is required"]
    assert validate_schema({"items": []}, schema) == ["$.items needs at least 1 item(s)"]
    assert validate_schema([], schema) == ["$ must be object, got list"]


def test_items_of_reads_a_list_an_items_key_or_a_single_object() -> None:
    assert items_of([1, 2]) == [1, 2]
    assert items_of({"items": ["a"], "other": 1}) == ["a"]
    assert items_of({"file": "a.py"}) == [{"file": "a.py"}]
    assert items_of(None) == []


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


def test_the_workflow_tool_is_off_and_the_limits_have_defaults() -> None:
    config = NerdvanaSettings().workflow
    assert (config.enabled, config.max_parallel, config.max_agents) == (False, 4, 50)


def test_the_enabled_flag_is_read_from_the_config_and_a_typo_stops_startup(tmp_path: Path) -> None:
    good = tmp_path / "good.yml"
    good.write_text("workflow:\n  enabled: true\n  max_parallel: 2\n", encoding="utf-8")
    loaded = NerdvanaSettings.load(str(good))
    assert loaded.workflow.enabled is True and loaded.workflow.max_parallel == 2
    bad = tmp_path / "bad.yml"
    bad.write_text("workflow:\n  enabled: maybe\n", encoding="utf-8")
    with pytest.raises(SettingsLoadError, match="workflow.enabled"):
        NerdvanaSettings.load(str(bad))
