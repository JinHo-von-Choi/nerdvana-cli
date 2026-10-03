"""A goal holds the agent to a command: the run ends when it passes, not when the model stops.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from nerdvana_cli.core.agent_loop import AgentLoop
from nerdvana_cli.core.config.settings import NerdvanaSettings
from nerdvana_cli.core.state.goal import ACTIVE, MET, PAUSED, UNMET, Goal, load_goal
from nerdvana_cli.core.state.session import SessionStorage
from nerdvana_cli.core.tool import BaseTool, ToolContext, ToolRegistry
from nerdvana_cli.providers.base import ProviderEvent
from nerdvana_cli.types import ToolResult


class _Touch(BaseTool[Any]):
    """Creates done.txt in the project, which is what the verification command looks for."""

    name             = "Touch"
    description_text = "create done.txt"

    async def call(self, args: Any, context: ToolContext, can_use_tool: Any = None, on_progress: Any = None) -> ToolResult:
        (Path(context.cwd) / "done.txt").write_text("done")
        return ToolResult(tool_use_id="", content="created")


def _say(text: str) -> list[ProviderEvent]:
    return [ProviderEvent(type="content_delta", content=text), ProviderEvent(type="done", stop_reason="end_turn")]


def _touch(n: int) -> list[ProviderEvent]:
    return [
        ProviderEvent(type="tool_use_complete", tool_use_id=f"t{n}", tool_name="Touch", tool_input_complete={}),
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


def _loop(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, provider: _Script, session_id: str = "goal") -> AgentLoop:
    monkeypatch.setenv("NERDVANA_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setattr(AgentLoop, "create_provider_from_settings", lambda self: provider)
    monkeypatch.setattr(AgentLoop, "build_system_prompt", lambda self: "system")
    project = tmp_path / "project"
    project.mkdir(exist_ok=True)
    registry = ToolRegistry()
    registry.register(_Touch())
    settings     = NerdvanaSettings()
    settings.cwd = str(project)
    return AgentLoop(settings=settings, registry=registry, session=SessionStorage(session_id=session_id, storage_dir=str(tmp_path / "sessions")))


async def _drain(loop: AgentLoop, prompt: str = "go") -> str:
    return "".join([chunk async for chunk in loop.run(prompt)])


async def test_without_a_goal_the_run_ends_when_the_model_stops(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    provider = _Script([_say("done")])
    loop     = _loop(monkeypatch, tmp_path, provider)
    await _drain(loop)
    assert loop.last_stop == "completed" and len(provider.payloads) == 1
    assert loop.verification_summary() is None


async def test_a_failed_check_sends_the_model_back_to_work_until_the_command_passes(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    provider = _Script([_say("finished"), _touch(1), _say("now it is finished")])
    loop     = _loop(monkeypatch, tmp_path, provider)
    loop.set_goal(Goal(objective="make done.txt", verify="test -f done.txt || { echo 'done.txt is missing'; exit 1; }"))
    output   = await _drain(loop)
    assert loop.last_stop == "completed"
    assert len(provider.payloads) == 3
    told = [str(m.get("content", "")) for m in provider.payloads[1] if m["role"] == "user"]
    assert any("[Verification]" in text and "done.txt is missing" in text and "attempt 1 of 5" in text for text in told)
    assert "Goal met" in output
    summary = loop.verification_summary()
    assert summary is not None and (summary["status"], summary["attempts"], summary["last_exit"]) == (MET, 2, 0)
    assert loop.signal_summary()["verify_failed"] == 1


async def test_the_goal_is_given_up_when_the_attempts_run_out(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    provider = _Script([])
    loop     = _loop(monkeypatch, tmp_path, provider)
    loop.set_goal(Goal(objective="impossible", verify="false", max_attempts=2))
    output   = await _drain(loop)
    assert loop.last_stop == "goal_unmet"
    assert "Goal not met after 2 verification attempts" in output
    assert len(provider.payloads) == 2
    assert loop.goal is not None and loop.goal.status == UNMET


async def test_a_paused_goal_is_not_enforced(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    provider = _Script([_say("done")])
    loop     = _loop(monkeypatch, tmp_path, provider)
    loop.set_goal(Goal(objective="x", verify="false", status=PAUSED))
    await _drain(loop)
    assert loop.last_stop == "completed" and loop.goal.attempts == 0  # type: ignore[union-attr]


async def test_a_met_goal_is_not_checked_again_on_the_next_prompt(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    provider = _Script([_say("a"), _say("b")])
    loop     = _loop(monkeypatch, tmp_path, provider)
    loop.set_goal(Goal(objective="x", verify="true"))
    await _drain(loop)
    await _drain(loop, "next")
    assert loop.goal.attempts == 1  # type: ignore[union-attr]


async def test_the_goal_is_saved_and_a_later_loop_for_the_same_session_finds_it(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    first = _loop(monkeypatch, tmp_path, _Script([_say("done")]), session_id="resume-me")
    first.settings.session.max_turns = 1  # stop after one verification so the goal is still active
    first.set_goal(Goal(objective="keep going", verify="false", max_attempts=5))
    await _drain(first)
    saved = load_goal("resume-me")
    assert saved is not None and saved.status == ACTIVE and saved.attempts == 1

    second = _loop(monkeypatch, tmp_path, _Script([_say("done")]), session_id="resume-me")
    assert second.goal is not None and second.goal.attempts == 1 and second.goal.verify == "false"


async def test_the_todo_guard_comes_before_the_verification(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import json

    from nerdvana_cli.core.state.todos import todos_dir

    provider = _Script([_say("done"), _say("done again")])
    loop     = _loop(monkeypatch, tmp_path, provider, session_id="todo-first")
    (todos_dir() / "todo-first.json").write_text(json.dumps({"todos": [{"content": "write tests", "status": "pending"}]}), encoding="utf-8")
    loop.settings.session.max_turns = 2
    loop.set_goal(Goal(objective="x", verify="true"))
    await _drain(loop)
    assert loop.goal.attempts == 0  # type: ignore[union-attr]
    told = [str(m.get("content", "")) for m in provider.payloads[1] if m["role"] == "user"]
    assert any("write tests" in text for text in told)


# ---------------------------------------------------------------------------
# Scope: edits outside what the goal is about ask first
# ---------------------------------------------------------------------------


class _Writer(BaseTool[Any]):
    name             = "FileWrite"
    description_text = "write"
    input_schema: dict[str, Any] = {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}

    def __init__(self) -> None:
        self.written: list[str] = []

    def parse_args(self, tool_input: dict[str, Any]) -> Any:
        from types import SimpleNamespace

        return SimpleNamespace(path=tool_input["path"])

    async def call(self, args: Any, context: Any, can_use_tool: Any = None, on_progress: Any = None) -> ToolResult:
        self.written.append(args.path)
        return ToolResult(tool_use_id="", content="written")


async def _write(path: str, scope: list[str] | None, answer: bool | None, tmp_path: Path) -> tuple[ToolResult, _Writer, Any]:
    from nerdvana_cli.core.hooks.hooks import HookEngine
    from nerdvana_cli.core.safety.policy import PermissionPolicy
    from nerdvana_cli.core.tool_executor import ToolExecutor

    tool     = _Writer()
    registry = ToolRegistry()
    registry.register(tool)
    asked: list[str] = []

    async def confirm(name: str, message: str) -> bool:
        asked.append(message)
        return bool(answer)

    executor = ToolExecutor(registry=registry, hooks=HookEngine(), settings=NerdvanaSettings(), policy=PermissionPolicy(trust_level="yolo"))
    context  = ToolContext(cwd=str(tmp_path), confirm=confirm if answer is not None else None)
    context.state["goal_scope"] = scope
    (result,) = await executor.run_batch([{"id": "1", "name": "FileWrite", "input": {"path": path}}], context)
    return result, tool, (executor, asked)


async def test_an_edit_inside_the_goals_scope_runs_without_a_question(tmp_path: Path) -> None:
    result, tool, (_, asked) = await _write("tests/test_a.py", ["tests"], True, tmp_path)
    assert not result.is_error and tool.written == ["tests/test_a.py"] and asked == []


async def test_an_edit_outside_the_scope_asks_and_follows_the_answer(tmp_path: Path) -> None:
    allowed, tool, (executor, asked) = await _write("src/a.py", ["tests"], True, tmp_path)
    assert not allowed.is_error and tool.written == ["src/a.py"]
    assert "outside the goal's scope (tests): src/a.py" in asked[0]
    refused, tool, (executor, _) = await _write("src/a.py", ["tests"], False, tmp_path)
    assert refused.is_error and tool.written == [] and "Permission denied by user" in refused.content
    assert executor.signals["out_of_goal_scope"] == 1


async def test_with_nobody_to_ask_an_edit_outside_the_scope_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.stdin.isatty", lambda: False, raising=False)
    result, tool, _ = await _write("src/a.py", ["tests"], None, tmp_path)
    assert result.is_error and tool.written == []


async def test_a_goal_without_a_scope_asks_nothing(tmp_path: Path) -> None:
    result, tool, (_, asked) = await _write("src/a.py", None, True, tmp_path)
    assert not result.is_error and asked == []


async def test_only_an_enforced_goal_hands_its_scope_to_the_tools(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    loop = _loop(monkeypatch, tmp_path, _Script([]))
    loop.set_goal(Goal(objective="x", verify="true", scope=["tests"]))
    assert loop._new_tool_context().state["goal_scope"] == ["tests"]
    loop.set_goal(Goal(objective="x", verify="true", scope=["tests"], status=PAUSED))
    assert loop._new_tool_context().state["goal_scope"] is None
    loop.set_goal(None)
    assert loop._new_tool_context().state["goal_scope"] is None
