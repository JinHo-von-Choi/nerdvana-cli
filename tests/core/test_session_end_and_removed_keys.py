"""SESSION_END delivery and the removed hook configuration keys.

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
from nerdvana_cli.core.hooks.hooks import HookContext, HookEvent, HookResult
from nerdvana_cli.core.state.session import SessionStorage
from nerdvana_cli.core.tool import ToolRegistry
from nerdvana_cli.providers.base import ProviderEvent
from nerdvana_cli.tools.registry import create_tool_registry


class _Done:
    async def stream(self, system_prompt: str, messages: Any, tools: Any) -> AsyncIterator[ProviderEvent]:
        yield ProviderEvent(type="done", stop_reason="end_turn")


def _loop(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> tuple[AgentLoop, list[HookContext]]:
    monkeypatch.setenv("NERDVANA_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setattr(AgentLoop, "create_provider_from_settings", lambda self: _Done())
    monkeypatch.setattr(AgentLoop, "build_system_prompt", lambda self: "system")
    settings     = NerdvanaSettings()
    settings.cwd = str(tmp_path)
    loop = AgentLoop(
        settings = settings,
        registry = ToolRegistry(),
        session  = SessionStorage(session_id="ending", storage_dir=str(tmp_path / "sessions")),
    )
    ended: list[HookContext] = []
    loop.hooks.register(HookEvent.SESSION_END, lambda ctx: ended.append(ctx) or HookResult())
    return loop, ended


async def test_session_end_fires_once(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    loop, ended = _loop(monkeypatch, tmp_path)
    async for _ in loop.run("hi"):
        pass

    loop.close_session("exit")
    loop.close_session("exit")

    assert len(ended) == 1
    assert ended[0].extra == {"reason": "exit", "session_id": "ending"}


def test_session_end_does_not_fire_for_a_session_that_never_started(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    loop, ended = _loop(monkeypatch, tmp_path)
    loop.close_session("exit")
    assert ended == []


async def test_reset_ends_the_session_and_the_next_one_can_end_too(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    loop, ended = _loop(monkeypatch, tmp_path)
    async for _ in loop.run("first"):
        pass
    loop.reset_session()
    async for _ in loop.run("second"):
        pass
    loop.close_session("exit")

    assert [ctx.extra["reason"] for ctx in ended] == ["reset", "exit"]


def test_removed_hook_keys_load_with_a_removed_key_warning(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("NERDVANA_CONFIG", raising=False)
    config = tmp_path / "nerdvana.yml"
    config.write_text(
        "hooks:\n  session_start: [builtin:context_injection]\n  before_tool: []\n  allow_project_hooks: false\n",
        encoding="utf-8",
    )

    settings = NerdvanaSettings.load(str(config))

    removed = {w.path for w in settings.load_warnings if w.kind == "removed_key"}
    assert removed == {"hooks.session_start", "hooks.before_tool"}
    assert all("no longer used" in w.format() for w in settings.load_warnings if w.kind == "removed_key")


def test_team_tools_are_not_registered() -> None:
    names = {t.name for t in create_tool_registry(settings=NerdvanaSettings()).all_tools()}
    assert "TeamCreate" not in names
    assert "SendMessage" not in names
    assert {"TaskGet", "TaskStop"} <= names
