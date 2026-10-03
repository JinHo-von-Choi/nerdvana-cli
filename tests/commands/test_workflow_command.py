"""`nerdvana workflow ...` through the real Typer app, with the sub-agent runner replaced by a fake.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from nerdvana_cli.core.subagent_config import SubagentConfig
from nerdvana_cli.core.workflow_store import RunStore
from nerdvana_cli.main import app

WORKFLOW = """
name: greet
description: Say hello to someone.
inputs:
  who: {default: world, description: whom to greet}
  mood:
steps:
  - id: say
    prompt: Say hello to ${inputs.who}, feeling ${inputs.mood}.
"""


@pytest.fixture()
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    directory = tmp_path / "project" / ".nerdvana" / "workflows"
    directory.mkdir(parents=True)
    (directory / "greet.yml").write_text(WORKFLOW, encoding="utf-8")
    monkeypatch.setenv("NERDVANA_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.chdir(tmp_path / "project")
    monkeypatch.setattr("nerdvana_cli.cli.runtime.resolve_run_provider", lambda settings: ("anthropic", False))
    return tmp_path / "project"


def _invoke(*args: str):  # type: ignore[no-untyped-def]
    return CliRunner().invoke(app, ["--no-update-check", "workflow", *args])


def test_list_shows_project_and_bundled_workflows_with_their_origin(project: Path) -> None:
    result = _invoke("list")
    assert result.exit_code == 0, result.output
    assert "greet" in result.output and "project" in result.output
    assert "review-fanout" in result.output and "bundled" in result.output


def test_show_describes_inputs_steps_and_the_read_only_default(project: Path) -> None:
    result = _invoke("show", "review-fanout")
    assert result.exit_code == 0, result.output
    assert "every agent is read-only" in result.output
    assert "input base" in result.output and "default HEAD" in result.output
    assert "step files" in result.output and "step confirm" in result.output and "3 reviewers" in result.output


def test_an_unknown_workflow_is_refused_with_the_available_names(project: Path) -> None:
    result = _invoke("show", "nope")
    assert result.exit_code == 2
    assert "unknown workflow 'nope'" in result.output and "greet" in result.output


def test_run_prints_the_final_output_and_the_run_id(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[SubagentConfig] = []

    async def fake(config: SubagentConfig, abort: object) -> tuple[str, int]:
        seen.append(config)
        config.cost_usd = 0.01
        return f"FINAL<{config.prompt}>", 5

    monkeypatch.setattr("nerdvana_cli.core.workflow_engine.run_subagent", fake)
    result = _invoke("run", "greet", "--input", "who=Ada", "-i", "mood=calm")
    assert result.exit_code == 0, result.output
    assert "FINAL<Say hello to Ada, feeling calm.>" in result.output
    assert "completed: run " in result.output
    run_id = result.output.split("completed: run ")[1].split(",")[0]
    assert RunStore(run_id).read_meta()["status"] == "completed"
    assert seen[0].settings.cwd == str(project) and seen[0].factories is not None


def test_a_missing_or_unknown_input_and_a_malformed_option_end_the_command_with_exit_code_2(project: Path) -> None:
    assert "input mood is required" in _invoke("run", "greet").output
    assert _invoke("run", "greet", "--input", "mood=x", "--input", "other=1").exit_code == 2
    assert _invoke("run", "greet", "--input", "mood").exit_code == 2


def test_a_failed_run_exits_1_and_says_how_to_resume_and_resume_continues_it(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workflow = project / ".nerdvana" / "workflows" / "two.yml"
    workflow.write_text("name: two\ninputs:\n  x: 1\nsteps:\n  - id: a\n    prompt: first ${inputs.x}\n  - id: b\n    prompt: second ${steps.a.output}\n", encoding="utf-8")
    prompts: list[str] = []
    broken = {"on": True}

    async def fake(config: SubagentConfig, abort: object) -> tuple[str, int]:
        prompts.append(config.prompt)
        if broken["on"] and config.prompt.startswith("second"):
            raise RuntimeError("provider down")
        return f"done({config.prompt})", 1

    monkeypatch.setattr("nerdvana_cli.core.workflow_engine.run_subagent", fake)
    first = _invoke("run", "two")
    assert first.exit_code == 1 and "provider down" in first.output
    assert "nerdvana workflow run two --resume " in first.output
    run_id = first.output.split("--resume ")[1].split()[0]

    broken["on"] = False
    prompts.clear()
    second = _invoke("run", "two", "--resume", run_id)
    assert second.exit_code == 0, second.output
    assert prompts == ["second done(first 1)"]                      # the first step was not run again
    assert "1 reused" in second.output


def test_resume_refuses_a_run_of_another_workflow_or_one_that_does_not_exist(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = RunStore("someone-elses")
    store.write_meta({"workflow": "other", "inputs": {}, "status": "failed"})
    assert "belongs to workflow 'other'" in _invoke("run", "greet", "--resume", "someone-elses", "-i", "mood=x").output
    assert "no stored run" in _invoke("run", "greet", "--resume", "missing", "-i", "mood=x").output
    assert _invoke("run", "greet", "--resume", "../escape", "-i", "mood=x").exit_code == 2


def test_the_ceiling_stops_the_run_with_exit_code_3(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (project / ".nerdvana" / "workflows" / "long.yml").write_text(
        "name: long\nsteps:\n  - id: a\n    prompt: one\n  - id: b\n    prompt: two ${steps.a.output}\n  - id: c\n    prompt: three ${steps.b.output}\n",
        encoding="utf-8",
    )

    async def fake(config: SubagentConfig, abort: object) -> tuple[str, int]:
        config.cost_usd = 0.7
        return "x", 1

    monkeypatch.setattr("nerdvana_cli.core.workflow_engine.run_subagent", fake)
    result = _invoke("run", "long", "--max-cost-usd", "1.0")
    assert result.exit_code == 3 and "stopped: run " in result.output and "cost ceiling" in result.output


def test_the_approval_mode_option_is_validated(project: Path) -> None:
    assert _invoke("run", "greet", "--approval-mode", "reckless").exit_code == 2
