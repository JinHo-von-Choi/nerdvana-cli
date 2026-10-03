"""Permission confirmation through a front end instead of the terminal.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Static

from nerdvana_cli.core.config.settings import NerdvanaSettings
from nerdvana_cli.core.delegation.subagent import label_confirm
from nerdvana_cli.core.delegation.task_state import TaskRegistry
from nerdvana_cli.core.execution.tool_executor import ToolExecutor
from nerdvana_cli.core.hooks.hooks import HookEngine
from nerdvana_cli.core.safety.policy import PermissionPolicy
from nerdvana_cli.core.tool import BaseTool, ToolCategory, ToolContext, ToolRegistry
from nerdvana_cli.tools.agent_tool import AgentTool, AgentToolArgs
from nerdvana_cli.types import ToolResult
from nerdvana_cli.ui.app import NerdvanaApp
from nerdvana_cli.ui.widgets import ConfirmScreen

# ---------------------------------------------------------------------------
# Executor
# ---------------------------------------------------------------------------


class _Writer(BaseTool[Any]):
    name             = "FileWrite"
    description_text = "write"
    category         = ToolCategory.WRITE

    def __init__(self) -> None:
        self.calls = 0

    async def call(self, args: Any, context: Any, can_use_tool: Any = None, on_progress: Any = None) -> ToolResult:
        self.calls += 1
        return ToolResult(tool_use_id="", content="written")


def _executor(tool: _Writer) -> ToolExecutor:
    registry = ToolRegistry()
    registry.register(tool)
    # strict trust asks before every write
    return ToolExecutor(
        registry = registry,
        hooks    = HookEngine(),
        settings = NerdvanaSettings(),
        policy   = PermissionPolicy(trust_level="strict"),
    )


async def _run(executor: ToolExecutor, context: ToolContext) -> ToolResult:
    results = await executor.run_batch([{"id": "c1", "name": "FileWrite", "input": {}}], context)
    return results[0]


async def test_front_end_allow_runs_the_tool_without_touching_stdin(tmp_path: Path) -> None:
    tool    = _Writer()
    confirm = AsyncMock(return_value=True)
    with patch.object(sys.stdin, "isatty", side_effect=AssertionError("stdin must not be consulted")):
        result = await _run(_executor(tool), ToolContext(cwd=str(tmp_path), confirm=confirm))
    assert not result.is_error
    assert tool.calls == 1
    confirm.assert_awaited_once()
    assert confirm.await_args.args[0] == "FileWrite"


async def test_front_end_deny_refuses_the_call(tmp_path: Path) -> None:
    tool   = _Writer()
    result = await _run(_executor(tool), ToolContext(cwd=str(tmp_path), confirm=AsyncMock(return_value=False)))
    assert result.is_error
    assert "denied" in result.content.lower()
    assert tool.calls == 0


async def test_a_failing_front_end_denies_instead_of_allowing(tmp_path: Path) -> None:
    tool   = _Writer()
    result = await _run(_executor(tool), ToolContext(cwd=str(tmp_path), confirm=AsyncMock(side_effect=RuntimeError("ui gone"))))
    assert result.is_error
    assert tool.calls == 0


async def test_without_a_front_end_a_missing_terminal_still_denies(tmp_path: Path) -> None:
    tool = _Writer()
    with patch.object(sys.stdin, "isatty", return_value=False):
        result = await _run(_executor(tool), ToolContext(cwd=str(tmp_path)))
    assert result.is_error
    assert tool.calls == 0


# ---------------------------------------------------------------------------
# Sub-agents
# ---------------------------------------------------------------------------


async def test_label_confirm_names_the_asking_agent() -> None:
    inner   = AsyncMock(return_value=True)
    wrapped = label_confirm(inner, "agent-7")
    assert wrapped is not None
    assert await wrapped("Bash", "runs a command") is True
    inner.assert_awaited_once_with("Bash", "[agent-7] runs a command")
    assert label_confirm(None, "agent-7") is None


async def test_agent_tool_hands_its_front_end_to_the_child() -> None:
    tasks   = TaskRegistry()
    tool    = AgentTool(settings=NerdvanaSettings(), task_registry=tasks)
    confirm = AsyncMock(return_value=True)
    context = ToolContext(task_registry=tasks, confirm=confirm)

    with patch("nerdvana_cli.tools.agent_tool.run_subagent", new_callable=AsyncMock, return_value=("ok", 1)) as run:
        await tool.call(AgentToolArgs(prompt="go"), context, None)

    child = run.await_args.args[0].confirm
    assert child is not None
    await child("FileWrite", "overwrites a.py")
    assert confirm.await_args.args[1].endswith("overwrites a.py")
    assert confirm.await_args.args[1].startswith("[")


async def test_a_loop_given_a_front_end_puts_it_on_the_tool_context(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from nerdvana_cli.core.loop.agent_loop import AgentLoop
    from nerdvana_cli.core.state.session import SessionStorage
    from nerdvana_cli.providers.base import ProviderEvent

    seen: list[Any] = []

    class _Provider:
        calls = 0

        async def stream(self, system_prompt: str, messages: Any, tools: Any) -> Any:
            type(self).calls += 1
            if type(self).calls == 1:
                yield ProviderEvent(type="tool_use_complete", tool_use_id="c1", tool_name="FileWrite", tool_input_complete={})
                yield ProviderEvent(type="done", stop_reason="tool_use")
            else:
                yield ProviderEvent(type="done", stop_reason="end_turn")

    monkeypatch.setenv("NERDVANA_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setattr(AgentLoop, "create_provider_from_settings", lambda self: _Provider())
    monkeypatch.setattr(AgentLoop, "build_system_prompt", lambda self: "system")
    tool = _Writer()
    registry = ToolRegistry()
    registry.register(tool)
    settings     = NerdvanaSettings()
    settings.cwd = str(tmp_path)
    confirm = AsyncMock(side_effect=lambda name, message: seen.append((name, message)) or True)
    loop = AgentLoop(
        settings   = settings,
        registry   = registry,
        session    = SessionStorage(session_id="confirm", storage_dir=str(tmp_path / "sessions")),
        on_confirm = confirm,
    )
    loop.policy.trust_level = "strict"

    async for _ in loop.run("write"):
        pass

    assert tool.calls == 1
    assert seen and seen[0][0] == "FileWrite"


# ---------------------------------------------------------------------------
# Modal
# ---------------------------------------------------------------------------


def _host(decisions: list[bool | None]) -> App[None]:
    class _Host(App[None]):
        def compose(self) -> ComposeResult:
            yield Static("host")

        def on_mount(self) -> None:
            self.push_screen(ConfirmScreen("FileWrite", "overwrites a.py [x]"), decisions.append)

    return _Host()


@pytest.mark.parametrize(("keys", "expected"), [(("y",), True), (("n",), False), (("escape",), False), (("enter",), False)])
async def test_modal_keys(keys: tuple[str, ...], expected: bool) -> None:
    decisions: list[bool | None] = []
    host = _host(decisions)
    async with host.run_test() as pilot:
        await pilot.pause()
        await pilot.press(*keys)
        await pilot.pause()
    assert decisions == [expected]


async def test_modal_buttons_decide() -> None:
    for button, expected in (("#confirm-allow", True), ("#confirm-deny", False)):
        decisions: list[bool | None] = []
        host = _host(decisions)
        async with host.run_test() as pilot:
            await pilot.pause()
            await pilot.click(button)
            await pilot.pause()
        assert decisions == [expected]


async def test_modal_shows_the_message_literally() -> None:
    decisions: list[bool | None] = []
    host = _host(decisions)
    async with host.run_test() as pilot:
        await pilot.pause()
        text = str(host.screen.query_one("#confirm-message", Static).render())
        assert "[x]" in text
        await pilot.press("n")


# ---------------------------------------------------------------------------
# App method: one dialog at a time
# ---------------------------------------------------------------------------


async def test_concurrent_requests_are_served_one_after_another() -> None:
    class _Host(App[None]):
        def __init__(self) -> None:
            super().__init__()
            self._confirm_lock = asyncio.Lock()

        _confirm_prompt = NerdvanaApp._confirm_prompt

        def compose(self) -> ComposeResult:
            yield Static("host")

    host = _Host()
    async with host.run_test() as pilot:
        first  = asyncio.create_task(host._confirm_prompt("A", "first"))
        second = asyncio.create_task(host._confirm_prompt("B", "second"))
        await pilot.pause()
        assert isinstance(host.screen, ConfirmScreen)
        assert "first" in str(host.screen.query_one("#confirm-message", Static).render())
        assert len(host.screen_stack) == 2
        await pilot.press("n")
        await pilot.pause()
        assert "second" in str(host.screen.query_one("#confirm-message", Static).render())
        await pilot.press("y")
        await pilot.pause()
        assert (await first, await second) == (False, True)



def test_escape_is_left_to_an_open_modal() -> None:
    from types import SimpleNamespace

    from nerdvana_cli.ui.app import NerdvanaApp
    from nerdvana_cli.ui.widgets.confirm_screen import ConfirmScreen

    modal_app = SimpleNamespace(screen=ConfirmScreen("Bash", "rm x"))
    assert NerdvanaApp.check_action(modal_app, "focus_input", ()) is False  # type: ignore[arg-type]
