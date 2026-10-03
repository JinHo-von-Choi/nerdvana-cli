"""The receipt of a run: files changed, verification, limits, cost per agent, problems.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from typer.testing import CliRunner

from nerdvana_cli.cli.receipt import RECEIPT_VERSION, build_receipt
from nerdvana_cli.core.agent_loop import AgentLoop
from nerdvana_cli.core.config.settings import NerdvanaSettings
from nerdvana_cli.core.hooks.hooks import HookEngine
from nerdvana_cli.core.telemetry.analytics import AnalyticsReader, AnalyticsWriter, CallOrigin
from nerdvana_cli.core.tool import BaseTool, ToolContext, ToolRegistry
from nerdvana_cli.core.tool_executor import ToolExecutor
from nerdvana_cli.main import app
from nerdvana_cli.providers.base import ProviderEvent
from nerdvana_cli.types import ToolResult


class _Edit(BaseTool[Any]):
    name             = "FileEdit"
    description_text = "edit"
    input_schema: dict[str, Any] = {"type": "object", "properties": {"path": {"type": "string"}, "apply": {"type": "boolean"}}, "required": ["path"]}

    def parse_args(self, tool_input: dict[str, Any]) -> Any:
        return SimpleNamespace(path=tool_input["path"], apply=tool_input.get("apply", True))

    async def call(self, args: Any, context: Any, can_use_tool: Any = None, on_progress: Any = None) -> ToolResult:
        return ToolResult(tool_use_id="", content="edited", is_error=args.path.startswith("bad"))


async def test_applied_edits_are_counted_per_file_and_failures_and_previews_are_not(tmp_path: Path) -> None:
    registry = ToolRegistry()
    registry.register(_Edit())
    executor = ToolExecutor(registry=registry, hooks=HookEngine(), settings=NerdvanaSettings())
    context  = ToolContext(cwd=str(tmp_path))
    for index, tool_input in enumerate([{"path": "a.py"}, {"path": "a.py"}, {"path": "b.py"}, {"path": "bad.py"}, {"path": "c.py", "apply": False}]):
        await executor.run_batch([{"id": str(index), "name": "FileEdit", "input": tool_input}], context)
    assert dict(executor.edited) == {"a.py": 2, "b.py": 1}


def _loop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AgentLoop:
    monkeypatch.setenv("NERDVANA_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setattr(AgentLoop, "create_provider_from_settings", lambda self: None)
    settings     = NerdvanaSettings()
    settings.cwd = str(tmp_path)
    return AgentLoop(settings=settings, registry=ToolRegistry())


def test_the_receipt_gathers_what_the_run_measured(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    loop = _loop(tmp_path, monkeypatch)
    loop.tool_executor.edited.update({"src/b.py": 1, "src/a.py": 3})
    loop.tool_executor.signals["cas_rejected"] = 2
    loop._signals["verify_failed"] = 1
    loop.settings.sandbox.mode = "auto"
    receipt = build_receipt(loop, {"command": "pytest", "status": "met", "attempts": 2, "last_exit": 0}, {"main": {"requests": 4, "cost_usd": 0.12}})
    assert receipt["receipt_version"] == RECEIPT_VERSION
    assert list(receipt["files_changed"]) == ["src/a.py", "src/b.py"] and receipt["files_changed"]["src/a.py"] == 3
    assert receipt["verification"]["status"] == "met"
    assert receipt["sandbox"]["mode"] == "auto" and receipt["cost_by_agent"]["main"]["requests"] == 4
    assert receipt["problems"] == {"cas_rejected": 2, "verify_failed": 1}


def test_the_cost_breakdown_counts_a_session_and_the_sub_agents_it_started(tmp_path: Path) -> None:
    db     = tmp_path / "a.sqlite"
    writer = AnalyticsWriter(db_path=db)
    writer.start_session("parent")
    usage = {"input_tokens": 1_000_000, "output_tokens": 0}
    writer.record_api_call("anthropic", "claude-sonnet-5-5", usage, CallOrigin())
    child = AnalyticsWriter(db_path=db)      # a sub-agent has a session of its own
    child.start_session("child-1")
    child.record_api_call("anthropic", "claude-sonnet-5-5", usage, CallOrigin(agent_id="a1", agent_type="Explore", parent_session_id="parent"))
    stranger = AnalyticsWriter(db_path=db)
    stranger.start_session("child-2")
    stranger.record_api_call("anthropic", "claude-sonnet-5-5", usage, CallOrigin(agent_id="a2", agent_type="Explore", parent_session_id="other"))
    breakdown = AnalyticsReader(db).cost_breakdown("parent")
    assert set(breakdown) == {"main", "Explore"} and breakdown["Explore"]["requests"] == 1
    assert AnalyticsReader(tmp_path / "none.sqlite").cost_breakdown("x") == {}


class _Writes:
    """Edits a file, then finishes."""

    def __init__(self) -> None:
        self.calls = 0

    async def stream(self, system_prompt: str, messages: Any, tools: Any) -> Any:
        self.calls += 1
        if self.calls == 1:
            yield ProviderEvent(type="tool_use_complete", tool_use_id="e1", tool_name="FileEdit", tool_input_complete={"path": "x.py"})
            yield ProviderEvent(type="done", stop_reason="tool_use")
        else:
            yield ProviderEvent(type="content_delta", content="done")
            yield ProviderEvent(type="done", stop_reason="end_turn")


def _env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, provider: Any) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("NERDVANA_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("NERDVANA_NO_UPDATE_CHECK", "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(AgentLoop, "build_system_prompt", lambda self: "system")
    monkeypatch.setattr(AgentLoop, "create_provider_from_settings", lambda self: provider)


def test_a_run_that_changed_files_carries_the_receipt_in_its_result(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch, tmp_path, _Writes())
    from nerdvana_cli.tools.registry import create_tool_registry

    original = create_tool_registry

    def registry(**kwargs: Any) -> ToolRegistry:
        reg = original(**kwargs)
        reg.register(_Edit())
        return reg

    monkeypatch.setattr("nerdvana_cli.cli.bootstrap.create_tool_registry", registry)
    result = CliRunner().invoke(app, ["run", "go", "--output-format", "json", "--approval-mode", "yolo"])
    assert result.exit_code == 0, result.output
    receipt = json.loads(result.stdout)["receipt"]
    assert receipt["files_changed"] == {"x.py": 1}
    assert receipt["sandbox"]["mode"] == "off" and receipt["receipt_version"] == 1


def test_a_run_that_changed_nothing_and_checked_nothing_has_no_receipt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    class _Quiet:
        async def stream(self, system_prompt: str, messages: Any, tools: Any) -> Any:
            yield ProviderEvent(type="content_delta", content="hi")
            yield ProviderEvent(type="done", stop_reason="end_turn")

    _env(monkeypatch, tmp_path, _Quiet())
    payload = json.loads(CliRunner().invoke(app, ["run", "go", "--output-format", "json"]).stdout)
    assert "receipt" not in payload


def test_a_goal_puts_its_verification_in_the_receipt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    class _Quiet:
        async def stream(self, system_prompt: str, messages: Any, tools: Any) -> Any:
            yield ProviderEvent(type="content_delta", content="hi")
            yield ProviderEvent(type="done", stop_reason="end_turn")

    _env(monkeypatch, tmp_path, _Quiet())
    payload = json.loads(CliRunner().invoke(app, ["run", "go", "--output-format", "json", "--verify", "true"]).stdout)
    assert payload["receipt"]["verification"]["status"] == "met" and payload["receipt"]["files_changed"] == {}
