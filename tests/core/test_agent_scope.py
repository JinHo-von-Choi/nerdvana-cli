"""Write scopes of agent definitions: the sandbox policy for commands and the edit scope for tools.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from nerdvana_cli.agents.builtin import BUILTIN_AGENTS
from nerdvana_cli.agents.registry import AgentDefinition, AgentTypeRegistry
from nerdvana_cli.core.config.settings import NerdvanaSettings
from nerdvana_cli.core.hooks.hooks import HookEngine
from nerdvana_cli.core.safety import sandbox
from nerdvana_cli.core.safety.agent_scope import apply_write_scope
from nerdvana_cli.core.safety.sandbox import SandboxPolicy, writable_paths
from nerdvana_cli.core.subagent_config import SubagentConfig
from nerdvana_cli.core.task_state import TaskRegistry
from nerdvana_cli.core.tool import BaseTool, ToolContext, ToolRegistry
from nerdvana_cli.core.tool_executor import ToolExecutor
from nerdvana_cli.tools.agent_tool import AgentTool, AgentToolArgs
from nerdvana_cli.tools.bash_tool import BashArgs, BashTool
from nerdvana_cli.types import ToolResult

# ---------------------------------------------------------------------------
# The sandbox policy
# ---------------------------------------------------------------------------


def test_project_and_scratch_can_be_switched_off_and_dev_always_stays(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    default = writable_paths(SandboxPolicy("auto"), str(project))
    assert str(project.resolve()) in default and "/tmp" in default
    nothing = writable_paths(SandboxPolicy("auto", project=False, scratch=False), str(project))
    assert nothing == ["/dev"]
    only_scratch = writable_paths(SandboxPolicy("auto", project=False, scratch=True), str(project))
    assert str(project.resolve()) not in only_scratch and "/tmp" in only_scratch


# ---------------------------------------------------------------------------
# Applying a definition
# ---------------------------------------------------------------------------


def _definition(**kw: Any) -> AgentDefinition:
    return AgentDefinition(agent_type="t", description="", **kw)


def test_none_closes_everything_and_turns_the_sandbox_on_in_auto_mode() -> None:
    settings = NerdvanaSettings()
    assert apply_write_scope(settings, _definition(write_scope="none"))
    box = settings.sandbox
    assert (box.project_writable, box.scratch_writable, box.write_paths, box.edit_scope, box.mode) == (False, False, [], [], "auto")


def test_a_path_list_allows_only_those_paths_and_scratch(tmp_path: Path) -> None:
    settings = NerdvanaSettings()
    apply_write_scope(settings, _definition(write_scope=["tests", "docs/api"]), str(tmp_path))
    box = settings.sandbox
    assert box.project_writable is False and box.scratch_writable is True
    assert box.write_paths == [str((tmp_path / "tests").resolve()), str((tmp_path / "docs/api").resolve())]
    assert box.edit_scope == ["tests", "docs/api"]


def test_a_session_set_to_require_keeps_its_mode_and_no_scope_changes_nothing() -> None:
    settings = NerdvanaSettings()
    settings.sandbox.mode = "require"
    apply_write_scope(settings, _definition(write_scope="none"))
    assert settings.sandbox.mode == "require"
    fresh = NerdvanaSettings()
    assert not apply_write_scope(fresh, _definition()) and fresh.sandbox.mode == "off"
    assert not apply_write_scope(fresh, _definition(write_scope="project"))


def test_network_is_applied_on_its_own() -> None:
    settings = NerdvanaSettings()
    assert apply_write_scope(settings, _definition(network=False))
    assert settings.sandbox.network is False and settings.sandbox.mode == "auto"


def test_the_read_only_built_in_agents_write_nothing() -> None:
    scopes = {d.agent_type: d.write_scope for d in BUILTIN_AGENTS}
    assert scopes["Explore"] == scopes["Plan"] == scopes["code-reviewer"] == "none"
    assert scopes["general-purpose"] == ""


def test_a_definition_file_can_carry_a_scope_and_bad_values_mean_none(tmp_path: Path) -> None:
    (tmp_path / "a.yml").write_text("name: a\nwrite_scope: [tests, 'docs/x']\nnetwork: false\n", encoding="utf-8")
    (tmp_path / "b.yml").write_text("name: b\nwrite_scope: none\n", encoding="utf-8")
    (tmp_path / "c.yml").write_text("name: c\nwrite_scope: 7\nnetwork: maybe\n", encoding="utf-8")
    registry = AgentTypeRegistry()
    registry.load_from_dir(str(tmp_path))
    a, b, c = registry.get("a"), registry.get("b"), registry.get("c")
    assert (a.write_scope, a.network) == (["tests", "docs/x"], False)  # type: ignore[union-attr]
    assert (b.write_scope, b.network) == ("none", None)  # type: ignore[union-attr]
    assert (c.write_scope, c.network) == ("", None)  # type: ignore[union-attr]


async def test_the_agent_tool_narrows_the_child_by_its_agent_type() -> None:
    registry = TaskRegistry()

    async def fake(config: SubagentConfig, abort: object) -> tuple[str, int]:
        return "ok", 1

    with patch("nerdvana_cli.tools.agent_tool.run_subagent", new=AsyncMock(side_effect=fake)) as run:
        await AgentTool(settings=NerdvanaSettings(), task_registry=registry).call(
            AgentToolArgs(prompt="p", subagent_type="Explore"), ToolContext(cwd=".", task_registry=registry), can_use_tool=None,
        )
    box = run.await_args.args[0].settings.sandbox
    assert box.project_writable is False and box.edit_scope == []


# ---------------------------------------------------------------------------
# Edit tools are held to the scope by the executor
# ---------------------------------------------------------------------------


class _Edit(BaseTool[Any]):
    name             = "FileEdit"
    description_text = "edit"
    input_schema: dict[str, Any] = {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}

    def __init__(self) -> None:
        self.paths: list[str] = []

    def parse_args(self, tool_input: dict[str, Any]) -> Any:
        return SimpleNamespace(path=tool_input["path"])

    async def call(self, args: Any, context: Any, can_use_tool: Any = None, on_progress: Any = None) -> ToolResult:
        self.paths.append(getattr(args, "path", None) or args.relative_path)
        return ToolResult(tool_use_id="", content="edited")


class _SymbolEdit(_Edit):
    name = "replace_symbol_body"
    input_schema: dict[str, Any] = {"type": "object", "properties": {"path": {"type": "string"}, "apply": {"type": "boolean"}}, "required": ["path"]}

    def parse_args(self, tool_input: dict[str, Any]) -> Any:
        return SimpleNamespace(relative_path=tool_input["path"], apply=bool(tool_input.get("apply", False)))


async def _run(tool: _Edit, tool_input: dict[str, Any], scope: list[str] | None, tmp_path: Path) -> ToolResult:
    registry = ToolRegistry()
    registry.register(tool)
    executor = ToolExecutor(registry=registry, hooks=HookEngine(), settings=NerdvanaSettings())
    context  = ToolContext(cwd=str(tmp_path))
    context.state["edit_scope"] = scope
    (result,) = await executor.run_batch([{"id": "1", "name": tool.name, "input": tool_input}], context)
    return result


async def test_edits_inside_the_scope_run_and_outside_it_are_refused(tmp_path: Path) -> None:
    tool = _Edit()
    ok   = await _run(tool, {"path": "tests/test_a.py"}, ["tests"], tmp_path)
    assert not ok.is_error and tool.paths == ["tests/test_a.py"]
    bad = await _run(tool, {"path": "src/a.py"}, ["tests"], tmp_path)
    assert bad.is_error and "edit scope (tests)" in bad.content and tool.paths == ["tests/test_a.py"]


async def test_traversal_and_look_alike_directories_do_not_pass(tmp_path: Path) -> None:
    tool = _Edit()
    for path in ("tests/../src/a.py", "tests_extra/a.py", "../outside.py"):
        assert (await _run(tool, {"path": path}, ["tests"], tmp_path)).is_error, path
    assert tool.paths == []


async def test_an_empty_scope_allows_no_edit_and_no_scope_allows_any(tmp_path: Path) -> None:
    tool = _Edit()
    assert (await _run(tool, {"path": "a.py"}, [], tmp_path)).is_error
    assert not (await _run(tool, {"path": "a.py"}, None, tmp_path)).is_error


async def test_symbol_edit_tools_are_covered_and_a_preview_is_not_an_edit(tmp_path: Path) -> None:
    tool = _SymbolEdit()
    assert (await _run(tool, {"path": "src/a.py", "apply": True}, ["tests"], tmp_path)).is_error
    assert tool.paths == []
    assert not (await _run(tool, {"path": "src/a.py", "apply": False}, ["tests"], tmp_path)).is_error


def test_the_signals_name_a_refusal_by_scope() -> None:
    from nerdvana_cli.core.signals import OUT_OF_SCOPE, classify_result

    assert classify_result("Outside this agent's edit scope (tests): src/a.py", True) == [OUT_OF_SCOPE]


# ---------------------------------------------------------------------------
# Commands, against the real kernel
# ---------------------------------------------------------------------------


@pytest.mark.skipif(sandbox.landlock_abi() < 1, reason="the kernel has no Landlock")
async def test_a_command_of_a_scope_none_agent_cannot_write_in_the_project(tmp_path: Path) -> None:
    from nerdvana_cli.core.agent_loop import AgentLoop

    settings     = NerdvanaSettings()
    settings.cwd = str(tmp_path)
    apply_write_scope(settings, _definition(write_scope="none"))
    holder  = type("Loop", (), {"settings": settings})()
    context = ToolContext(cwd=str(tmp_path))
    context.state["sandbox"] = AgentLoop._sandbox_policy(holder)  # type: ignore[arg-type]
    result = await BashTool().call(BashArgs("echo x > blocked.txt; echo status=$?"), context)
    assert not (tmp_path / "blocked.txt").exists()
    assert "Permission denied" in result.content
