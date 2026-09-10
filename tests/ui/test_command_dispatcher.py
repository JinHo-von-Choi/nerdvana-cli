"""Tests for the TUI slash-command dispatcher.

작성자: 최진호
작성일: 2026-09-11
"""

from __future__ import annotations

from typing import Any

import pytest

from nerdvana_cli.ui import command_dispatcher as cd

# ---------------------------------------------------------------------------
# Doubles
# ---------------------------------------------------------------------------


class _Input:
    def __init__(self) -> None:
        self.value      = ""
        self.submitted  = False

    def action_submit(self) -> None:
        self.submitted = True


class _Skill:
    def __init__(self, name: str = "deep-research", body: str = "SKILL BODY") -> None:
        self.name = name
        self.body = body


class _SkillLoader:
    def __init__(self, skill: _Skill | None) -> None:
        self._skill = skill

    def get_by_trigger(self, trigger: str) -> _Skill | None:
        return self._skill


class _AgentLoop:
    def __init__(self, skill: _Skill | None) -> None:
        self.skill_loader        = _SkillLoader(skill)
        self.activated: list[str] = []

    def activate_skill(self, body: str) -> None:
        self.activated.append(body)


class _App:
    """Minimal stand-in for NerdvanaApp."""

    def __init__(self, skill: _Skill | None = None, with_loop: bool = True) -> None:
        self._agent_loop         = _AgentLoop(skill) if with_loop else None
        self.messages: list[str] = []
        self.exited              = False
        self.input_widget        = _Input()
        self.deferred: list[Any] = []

    def exit(self) -> None:
        self.exited = True

    def _add_chat_message(self, message: str, **kwargs: Any) -> None:
        self.messages.append(message)

    def query_one(self, selector: str, widget_type: type | None = None) -> Any:
        assert selector == "#user-input"
        return self.input_widget

    def call_later(self, callback: Any, *args: Any) -> None:
        self.deferred.append(callback)
        callback(*args)

    @property
    def last(self) -> str:
        return self.messages[-1]


# ---------------------------------------------------------------------------
# Handler map
# ---------------------------------------------------------------------------


class TestHandlerMap:
    def test_every_entry_is_callable(self) -> None:
        handlers = cd._build_handler_map()
        assert handlers
        assert all(callable(fn) for fn in handlers.values())

    def test_every_key_is_a_slash_command(self) -> None:
        assert all(name.startswith("/") for name in cd._build_handler_map())

    def test_covers_the_memory_and_session_surface(self) -> None:
        handlers = cd._build_handler_map()
        assert {
            "/undo", "/redo", "/checkpoints", "/memories", "/route-knowledge",
            "/model", "/clear", "/help", "/mode", "/health",
        } <= set(handlers)

    def test_setup_is_an_alias_of_init(self) -> None:
        handlers = cd._build_handler_map()
        assert handlers["/setup"] is handlers["/init"]

    def test_map_is_rebuilt_per_call(self) -> None:
        assert cd._build_handler_map() is not cd._build_handler_map()


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


class TestDispatch:
    @pytest.mark.parametrize("alias", ["/quit", "/exit", "/q", "/QUIT"])
    async def test_exit_aliases(self, alias: str) -> None:
        app = _App()
        await cd.dispatch_command(app, alias)
        assert app.exited is True
        assert app.messages == []

    async def test_routes_to_handler_with_arguments(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        seen: list[tuple[Any, str]] = []

        async def _handler(app: Any, args: str) -> None:
            seen.append((app, args))

        monkeypatch.setattr(
            cd, "_build_handler_map", lambda: {"/memories": _handler},
        )
        app = _App()
        await cd.dispatch_command(app, "/memories --stale --days 5")
        assert seen == [(app, "--stale --days 5")]

    async def test_command_lookup_is_case_insensitive(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        called = False

        async def _handler(app: Any, args: str) -> None:
            nonlocal called
            called = True

        monkeypatch.setattr(cd, "_build_handler_map", lambda: {"/help": _handler})
        await cd.dispatch_command(_App(), "/HELP")
        assert called is True

    async def test_handler_receives_empty_args_when_none_given(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        seen: list[str] = []

        async def _handler(app: Any, args: str) -> None:
            seen.append(args)

        monkeypatch.setattr(cd, "_build_handler_map", lambda: {"/clear": _handler})
        await cd.dispatch_command(_App(), "/clear")
        assert seen == [""]


# ---------------------------------------------------------------------------
# Skill fallback
# ---------------------------------------------------------------------------


class TestSkillFallback:
    async def test_activates_matching_skill(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(cd, "_build_handler_map", dict)
        app = _App(skill=_Skill())
        await cd.dispatch_command(app, "/deep-research")

        assert app._agent_loop is not None
        assert app._agent_loop.activated == ["SKILL BODY"]
        assert "Skill activated: deep-research" in app.last
        assert app.input_widget.submitted is False

    async def test_forwards_arguments_into_the_input(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(cd, "_build_handler_map", dict)
        app = _App(skill=_Skill())
        await cd.dispatch_command(app, "/deep-research transformer scaling")

        assert app.input_widget.value == "transformer scaling"
        assert app.input_widget.submitted is True

    async def test_unknown_command_without_matching_skill(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(cd, "_build_handler_map", dict)
        app = _App(skill=None)
        await cd.dispatch_command(app, "/nope")
        assert "Unknown command: /nope" in app.last

    async def test_unknown_command_without_agent_loop(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(cd, "_build_handler_map", dict)
        app = _App(with_loop=False)
        await cd.dispatch_command(app, "/nope")
        assert "Unknown command: /nope" in app.last
