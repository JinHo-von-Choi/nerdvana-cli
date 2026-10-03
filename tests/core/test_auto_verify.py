"""Without a goal, a run that changed files is checked with the project's own test command (goal.auto_verify).

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from nerdvana_cli.core.config.settings import NerdvanaSettings
from nerdvana_cli.core.delegation.subagent import run_subagent
from nerdvana_cli.core.loop import auto_verify
from nerdvana_cli.core.loop.agent_loop import AgentLoop
from nerdvana_cli.core.loop.auto_verify import detect_test_command
from nerdvana_cli.core.loop.subagent_config import SubagentConfig
from nerdvana_cli.core.state.goal import MET, PAUSED, UNMET, Goal
from nerdvana_cli.core.state.session import SessionStorage
from nerdvana_cli.core.tool import BaseTool, ToolContext, ToolRegistry
from nerdvana_cli.providers.base import ProviderEvent
from nerdvana_cli.types import ToolResult

# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------


@pytest.fixture()
def installed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every program looks installed, so detection depends on the project's files alone."""
    monkeypatch.setattr(auto_verify.shutil, "which", lambda program: f"/usr/bin/{program}")


def test_an_empty_directory_has_no_test_command(tmp_path: Path, installed: None) -> None:
    assert detect_test_command(str(tmp_path)) == ""


@pytest.mark.parametrize("marker", ["pyproject.toml", "pytest.ini"])
def test_a_python_project_is_checked_with_pytest(tmp_path: Path, installed: None, marker: str) -> None:
    (tmp_path / marker).write_text("", encoding="utf-8")
    assert detect_test_command(str(tmp_path)) == "pytest -q"


def test_python_tests_under_tests_are_enough_for_pytest(tmp_path: Path, installed: None) -> None:
    (tmp_path / "tests").mkdir()
    assert detect_test_command(str(tmp_path)) == ""
    (tmp_path / "tests" / "test_a.py").write_text("", encoding="utf-8")
    assert detect_test_command(str(tmp_path)) == "pytest -q"


def test_a_package_with_a_test_script_is_checked_with_npm(tmp_path: Path, installed: None) -> None:
    (tmp_path / "package.json").write_text(json.dumps({"scripts": {"test": "vitest run"}}), encoding="utf-8")
    assert detect_test_command(str(tmp_path)) == "npm test"


@pytest.mark.parametrize("package", [
    {"scripts": {"build": "tsc"}},
    {"scripts": {"test": 'echo "Error: no test specified" && exit 1'}},
    {"scripts": {"test": " "}},
    {"scripts": []},
    [],
])
def test_a_package_without_a_real_test_script_has_no_test_command(tmp_path: Path, installed: None, package: Any) -> None:
    (tmp_path / "package.json").write_text(json.dumps(package), encoding="utf-8")
    assert detect_test_command(str(tmp_path)) == ""


def test_an_unreadable_package_json_has_no_test_command(tmp_path: Path, installed: None) -> None:
    (tmp_path / "package.json").write_text("{not json", encoding="utf-8")
    assert detect_test_command(str(tmp_path)) == ""


def test_a_cargo_project_is_checked_with_cargo_test(tmp_path: Path, installed: None) -> None:
    (tmp_path / "Cargo.toml").write_text("", encoding="utf-8")
    assert detect_test_command(str(tmp_path)) == "cargo test"


def test_a_go_module_is_checked_with_go_test(tmp_path: Path, installed: None) -> None:
    (tmp_path / "go.mod").write_text("module x\n", encoding="utf-8")
    assert detect_test_command(str(tmp_path)) == "go test ./..."


def test_when_several_ecosystems_are_present_the_first_in_the_documented_order_wins(tmp_path: Path, installed: None) -> None:
    (tmp_path / "go.mod").write_text("module x\n", encoding="utf-8")
    (tmp_path / "Cargo.toml").write_text("", encoding="utf-8")
    assert detect_test_command(str(tmp_path)) == "cargo test"
    (tmp_path / "pyproject.toml").write_text("", encoding="utf-8")
    assert detect_test_command(str(tmp_path)) == "pytest -q"


def test_a_program_that_is_not_installed_means_no_check(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(auto_verify.shutil, "which", lambda program: None)
    (tmp_path / "Cargo.toml").write_text("", encoding="utf-8")
    assert detect_test_command(str(tmp_path)) == ""


# ---------------------------------------------------------------------------
# The gate in the loop
# ---------------------------------------------------------------------------


@dataclass
class _EditArgs:
    path: str = ""


class _Edit(BaseTool[Any]):
    """An edit tool: from its ``creates_on``-th call on it creates done.txt in the project, which is what the check looks for."""

    name             = "FileEdit"
    description_text = "create done.txt"
    args_class       = _EditArgs
    input_schema: dict[str, Any] = {"type": "object", "properties": {"path": {"type": "string"}}}

    def __init__(self) -> None:
        self.calls      = 0
        self.creates_on = 1

    async def call(self, args: Any, context: ToolContext, can_use_tool: Any = None, on_progress: Any = None) -> ToolResult:
        self.calls += 1
        if self.calls >= self.creates_on:
            (Path(context.cwd) / "done.txt").write_text("done")
        return ToolResult(tool_use_id="", content="edited")


def _say(text: str) -> list[ProviderEvent]:
    return [ProviderEvent(type="content_delta", content=text), ProviderEvent(type="done", stop_reason="end_turn")]


def _edit(n: int) -> list[ProviderEvent]:
    return [
        ProviderEvent(type="tool_use_complete", tool_use_id=f"e{n}", tool_name="FileEdit", tool_input_complete={"path": "done.txt"}),
        ProviderEvent(type="done", stop_reason="tool_use"),
    ]


class _Script:
    def __init__(self, responses: list[list[ProviderEvent]]) -> None:
        self.responses = list(responses)
        self.payloads: list[list[dict[str, Any]]] = []

    async def stream(self, system_prompt: str, messages: Any, tools: Any) -> AsyncIterator[ProviderEvent]:
        self.payloads.append([dict(m) for m in messages])
        for event in self.responses.pop(0) if self.responses else _say("still done"):
            yield event


def _settings(project: Path, *, auto: bool, max_attempts: int = 5) -> NerdvanaSettings:
    settings                   = NerdvanaSettings()
    settings.cwd               = str(project)
    settings.goal.auto_verify  = auto
    settings.goal.max_attempts = max_attempts
    return settings


def _loop(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, provider: _Script, command: str, *, auto: bool = True, max_attempts: int = 5) -> AgentLoop:
    monkeypatch.setenv("NERDVANA_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setattr(AgentLoop, "create_provider_from_settings", lambda self: provider)
    monkeypatch.setattr(AgentLoop, "build_system_prompt", lambda self: "system")
    monkeypatch.setattr("nerdvana_cli.core.loop.goal_gate.detect_test_command", lambda cwd: command)
    project = tmp_path / "project"
    project.mkdir(exist_ok=True)
    registry = ToolRegistry()
    registry.register(_Edit())
    return AgentLoop(
        settings = _settings(project, auto=auto, max_attempts=max_attempts),
        registry = registry,
        session  = SessionStorage(session_id="auto", storage_dir=str(tmp_path / "sessions")),
    )


async def _drain(loop: AgentLoop, prompt: str = "go") -> str:
    return "".join([chunk async for chunk in loop.run(prompt)])


CHECK = "test -f done.txt || { echo 'done.txt is missing'; exit 1; }"


async def test_a_passing_check_lets_a_run_that_changed_files_end(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    provider = _Script([_edit(1), _say("done")])
    loop     = _loop(monkeypatch, tmp_path, provider, CHECK)
    output   = await _drain(loop)
    assert loop.last_stop == "completed" and len(provider.payloads) == 2
    assert f"Verifying: {CHECK}" in output
    summary = loop.verification_summary()
    assert summary is not None and (summary["command"], summary["status"], summary["attempts"], summary["last_exit"]) == (CHECK, MET, 1, 0)
    assert loop.goal is None


async def test_a_failing_check_sends_the_model_back_and_a_later_pass_ends_the_run(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    provider = _Script([_edit(1), _say("finished"), _edit(2), _say("now finished")])
    loop     = _loop(monkeypatch, tmp_path, provider, CHECK)
    loop.registry.get("FileEdit").creates_on = 2   # type: ignore[union-attr]
    output   = await _drain(loop)
    assert loop.last_stop == "completed" and len(provider.payloads) == 4
    told = [str(m.get("content", "")) for m in provider.payloads[2] if m["role"] == "user"]
    assert any("[Verification]" in text and "done.txt is missing" in text and "attempt 1 of 5" in text for text in told)
    assert output.count("Verifying") == 2
    summary = loop.verification_summary()
    assert summary is not None and (summary["status"], summary["attempts"]) == (MET, 2)
    assert loop.signal_summary()["verify_failed"] == 1


async def test_a_check_that_never_passes_ends_the_run_as_unmet_at_the_attempt_limit(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    provider = _Script([_edit(1)])
    loop     = _loop(monkeypatch, tmp_path, provider, "false", max_attempts=3)
    output   = await _drain(loop)
    assert loop.last_stop == "goal_unmet"
    assert "Goal not met after 3 verification attempts" in output
    assert len(provider.payloads) == 4   # the edit, then one answer per attempt
    summary = loop.verification_summary()
    assert summary is not None and (summary["status"], summary["attempts"]) == (UNMET, 3)
    assert loop.signal_summary()["verify_failed"] == 3


async def test_each_failure_is_put_in_front_of_the_model(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    provider = _Script([_edit(1)])
    loop     = _loop(monkeypatch, tmp_path, provider, "echo missing-file; false", max_attempts=2)
    await _drain(loop)
    told = [str(m.get("content", "")) for m in provider.payloads[-1] if m["role"] == "user"]
    assert any("[Verification]" in text and "missing-file" in text and "attempt 1 of 2" in text for text in told)


async def test_a_run_that_changed_nothing_is_not_checked(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    provider = _Script([_say("nothing to do")])
    loop     = _loop(monkeypatch, tmp_path, provider, "false")
    output   = await _drain(loop)
    assert loop.last_stop == "completed" and "Verifying" not in output
    assert loop.verification_summary() is None


async def test_with_the_setting_off_a_run_that_changed_files_is_not_checked(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    provider = _Script([_edit(1), _say("done")])
    loop     = _loop(monkeypatch, tmp_path, provider, "false", auto=False)
    output   = await _drain(loop)
    assert loop.last_stop == "completed" and "Verifying" not in output
    assert loop.verification_summary() is None
    assert "verify_failed" not in loop.signal_summary()


async def test_without_a_detected_command_there_is_no_gate(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    provider = _Script([_edit(1), _say("done")])
    loop     = _loop(monkeypatch, tmp_path, provider, "")
    output   = await _drain(loop)
    assert loop.last_stop == "completed" and "Verifying" not in output


async def test_an_explicit_goal_takes_the_place_of_the_automatic_check(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    provider = _Script([_edit(1), _say("done")])
    loop     = _loop(monkeypatch, tmp_path, provider, "false")
    loop.set_goal(Goal(objective="x", verify="true", status=PAUSED))
    output   = await _drain(loop)
    assert loop.last_stop == "completed" and "Verifying" not in output
    assert loop.goal is not None and loop.goal.attempts == 0

    provider.responses = [_edit(2), _say("done again")]
    loop.set_goal(Goal(objective="x", verify="true"))
    await _drain(loop, "again")
    assert loop.goal.attempts == 1  # type: ignore[union-attr]


async def test_the_automatic_check_is_not_saved_as_the_goal_of_the_session(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from nerdvana_cli.core.state.goal import load_goal

    loop = _loop(monkeypatch, tmp_path, _Script([_edit(1), _say("done")]), CHECK)
    await _drain(loop)
    assert load_goal("auto") is None


async def test_a_passed_check_is_not_repeated_until_more_files_change(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    provider = _Script([_edit(1), _say("a"), _say("b"), _edit(2), _say("c")])
    loop     = _loop(monkeypatch, tmp_path, provider, CHECK)
    first    = await _drain(loop)
    second   = await _drain(loop, "just talk")
    third    = await _drain(loop, "edit again")
    assert first.count("Verifying") == 1
    assert "Verifying" not in second
    assert third.count("Verifying") == 1


async def test_the_attempts_start_again_with_each_run(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    provider = _Script([_edit(1)])
    loop     = _loop(monkeypatch, tmp_path, provider, "false", max_attempts=1)
    await _drain(loop)
    assert loop.last_stop == "goal_unmet"
    provider.responses = [_edit(2)]
    await _drain(loop, "try again")
    summary = loop.verification_summary()
    assert loop.last_stop == "goal_unmet" and summary is not None and summary["attempts"] == 1


async def test_a_sub_agent_is_not_held_to_the_check(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    provider = _Script([_edit(1), _say("done")])
    monkeypatch.setenv("NERDVANA_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setattr(AgentLoop, "create_provider_from_settings", lambda self: provider)
    monkeypatch.setattr(AgentLoop, "build_system_prompt", lambda self: "system")
    monkeypatch.setattr("nerdvana_cli.core.loop.goal_gate.detect_test_command", lambda cwd: "false")
    project = tmp_path / "project"
    project.mkdir()
    registry = ToolRegistry()
    registry.register(_Edit())
    config = SubagentConfig(agent_id="sub", name="worker", prompt="do it", settings=_settings(project, auto=True), registry=registry)
    await run_subagent(config, asyncio.Event())
    assert config.stopped_for == "completed" and len(provider.payloads) == 2
