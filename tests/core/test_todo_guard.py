"""Todo continuation guard and the end-of-turn nudge cap.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from nerdvana_cli.core.agent_loop import AgentLoop
from nerdvana_cli.core.config.settings import NerdvanaSettings
from nerdvana_cli.core.state.session import SessionStorage
from nerdvana_cli.core.state.todos import CONTINUE, NONE, STALLED, TodoGuard, sanitize_session_id
from nerdvana_cli.core.tool import ToolRegistry
from nerdvana_cli.providers.base import ProviderEvent
from nerdvana_cli.types import Role


def _write(directory: Path, session: str, statuses: list[str]) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    todos = [{"content": f"item {i}", "status": s, "activeForm": f"doing {i}"} for i, s in enumerate(statuses)]
    (directory / f"{session}.json").write_text(json.dumps({"todos": todos}), encoding="utf-8")


def test_no_list_or_all_done_lets_the_turn_end(tmp_path: Path) -> None:
    guard = TodoGuard()
    assert guard.check("s", tmp_path).kind == NONE
    _write(tmp_path, "s", ["completed", "completed"])
    assert guard.check("s", tmp_path).kind == NONE


def test_open_items_ask_to_continue_and_name_them(tmp_path: Path) -> None:
    _write(tmp_path, "s", ["completed", "in_progress", "pending"])
    decision = TodoGuard().check("s", tmp_path)
    assert decision.kind == CONTINUE
    assert "item 1" in decision.message
    assert "item 2" in decision.message
    assert "item 0" not in decision.message


def test_three_nudges_without_progress_stop_the_guard(tmp_path: Path) -> None:
    _write(tmp_path, "s", ["pending", "pending"])
    guard = TodoGuard(max_stalls=3)
    kinds = [guard.check("s", tmp_path).kind for _ in range(4)]
    assert kinds == [CONTINUE, CONTINUE, CONTINUE, STALLED]


def test_progress_resets_the_stall_count(tmp_path: Path) -> None:
    guard = TodoGuard(max_stalls=2)
    _write(tmp_path, "s", ["pending", "pending", "pending"])
    guard.check("s", tmp_path)
    guard.check("s", tmp_path)
    _write(tmp_path, "s", ["completed", "pending", "pending"])
    assert guard.check("s", tmp_path).kind == CONTINUE
    assert guard.check("s", tmp_path).kind == CONTINUE
    assert guard.check("s", tmp_path).kind == STALLED


def test_unsafe_session_ids_fall_back() -> None:
    assert sanitize_session_id("../x") == "default"
    assert sanitize_session_id("..") == "default"
    assert sanitize_session_id("ok-1") == "ok-1"


def test_corrupt_list_counts_as_empty(tmp_path: Path) -> None:
    (tmp_path / "s.json").write_text("{not json", encoding="utf-8")
    assert TodoGuard().check("s", tmp_path).kind == NONE


class _AlwaysDone:
    def __init__(self, text: str = "done") -> None:
        self.calls = 0
        self.text  = text

    async def stream(self, system_prompt: str, messages: Any, tools: Any) -> AsyncIterator[ProviderEvent]:
        self.calls += 1
        yield ProviderEvent(type="content_delta", content=self.text)
        yield ProviderEvent(type="done", stop_reason="end_turn")


def _loop(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, provider: Any) -> AgentLoop:
    monkeypatch.setenv("NERDVANA_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setattr(AgentLoop, "create_provider_from_settings", lambda self: provider)
    monkeypatch.setattr(AgentLoop, "build_system_prompt", lambda self: "system")
    settings     = NerdvanaSettings()
    settings.cwd = str(tmp_path)
    return AgentLoop(
        settings = settings,
        registry = ToolRegistry(),
        session  = SessionStorage(session_id="todo-s", storage_dir=str(tmp_path / "sessions")),
    )


async def test_loop_keeps_going_while_todos_are_open_then_reports(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    provider = _AlwaysDone()
    loop     = _loop(monkeypatch, tmp_path, provider)
    _write(tmp_path / "data" / "todos", "todo-s", ["pending"])

    output = "".join([c async for c in loop.run("work")])

    assert provider.calls == 4
    nudges = [m for m in loop.state.messages if m.role == Role.USER and "unfinished todo" in str(m.content)]
    assert len(nudges) == 3
    assert "Stopped" in output


async def test_loop_ends_normally_without_todos(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    provider = _AlwaysDone()
    loop     = _loop(monkeypatch, tmp_path, provider)
    async for _ in loop.run("work"):
        pass
    assert provider.calls == 1


async def test_unfinished_marker_hook_is_capped_per_prompt(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    provider = _AlwaysDone("TODO: finish later")
    loop     = _loop(monkeypatch, tmp_path, provider)
    async for _ in loop.run("work"):
        pass
    assert provider.calls <= 4
