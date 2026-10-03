"""The /context, /history slash commands and the nerdvana context and history commands.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from rich.text import Text
from typer.testing import CliRunner

from nerdvana_cli.cli.history_search import fts5_available
from nerdvana_cli.commands import context_command, history_command
from nerdvana_cli.core.config.settings import SessionConfig
from nerdvana_cli.core.state.session import SessionStorage
from nerdvana_cli.main import app as cli
from nerdvana_cli.types import Message, Role
from nerdvana_cli.ui import command_dispatcher


class _App:
    """Stand-in for NerdvanaApp: records what reached the chat pane."""

    def __init__(self, loop: Any = None) -> None:
        self._agent_loop             = loop
        self._profile_manager        = None
        self.settings                = SimpleNamespace(cwd="/tmp", session=SimpleNamespace(default_mode="interactive", default_context="standalone"))
        self.messages: list[str]     = []

    def _add_chat_message(self, message: str, **kwargs: Any) -> None:
        self.messages.append(message)


def _tool(name: str) -> Any:
    return SimpleNamespace(name=name, description_text="describes " + name, input_schema={"type": "object"})


def _loop() -> Any:
    tools    = [_tool("FileRead"), _tool("Bash")]
    registry = SimpleNamespace(all_tools=lambda: tools, get=lambda name: None)
    messages = [
        Message(role=Role.USER, content="read it"),
        Message(role=Role.ASSISTANT, content="", tool_uses=[{"id": "a", "name": "FileRead", "input": {}}]),
        Message(role=Role.TOOL, content="body " * 300, tool_use_id="a"),
    ]
    return SimpleNamespace(
        registry=registry, policy=SimpleNamespace(is_visible=lambda name: True), state=SimpleNamespace(messages=messages),
        settings=SimpleNamespace(session=SessionConfig(max_context_tokens=100_000)),
        build_system_prompt=lambda: "base instructions\n\n# Skills\n\n- a: one",
        _sticky_session_context="snapshot of the workspace", _role_prompt="", _active_skill=None,
    )


@pytest.fixture
def data_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("NERDVANA_DATA_HOME", str(tmp_path / "home"))
    (tmp_path / "home" / "sessions").mkdir(parents=True)
    return tmp_path / "home"


def _store(data_home: Path, session_id: str, texts: list[str]) -> None:
    storage = SessionStorage(session_id=session_id, storage_dir=str(data_home / "sessions"))
    for text in texts:
        storage.record_user_message(text)


class TestSlashContext:
    @pytest.mark.asyncio
    async def test_bare_context_shows_the_breakdown_and_then_the_profile(self) -> None:
        app = _App(_loop())
        await context_command.handle_context(app, "")
        assert "Context window" in app.messages[0] and "FileRead" in app.messages[0]
        assert "session context (workspace snapshot, memory hint, hook output)" in app.messages[0]
        assert "Context :" in app.messages[1]

    @pytest.mark.asyncio
    async def test_usage_shows_only_the_breakdown(self) -> None:
        app = _App(_loop())
        await context_command.handle_context(app, "usage")
        assert len(app.messages) == 1 and "Tool declarations" in app.messages[0]

    @pytest.mark.asyncio
    async def test_square_brackets_in_the_text_are_not_read_as_markup(self) -> None:
        loop = _loop()
        loop.registry = SimpleNamespace(all_tools=lambda: [_tool("odd[bold]name")], get=lambda name: None)
        app = _App(loop)
        await context_command.handle_context(app, "usage")
        assert "odd[bold]name" in Text.from_markup(app.messages[0]).plain

    @pytest.mark.asyncio
    async def test_other_arguments_still_reach_the_profile_commands(self) -> None:
        app = _App(_loop())
        await context_command.handle_context(app, "list")
        assert "Available contexts" in app.messages[0] and len(app.messages) == 1

    @pytest.mark.asyncio
    async def test_without_a_session_the_command_says_so(self) -> None:
        app = _App(None)
        await context_command.handle_context(app, "usage")
        assert "No active session" in app.messages[0]

    def test_the_dispatcher_routes_both_slash_commands(self) -> None:
        table = command_dispatcher._build_handler_map()
        assert table["/context"] is context_command.handle_context
        assert table["/history"] is history_command.handle_history


class TestSlashHistory:
    @pytest.mark.asyncio
    async def test_a_query_prints_matching_lines(self, data_home: Path) -> None:
        _store(data_home, "s1", ["the migration plan for billing"])
        app = _App()
        await history_command.handle_history(app, "migration billing")
        assert app.messages[0].startswith("s1  ") and "migration plan" in app.messages[0]

    @pytest.mark.asyncio
    async def test_options_are_read_and_quotes_group_words(self, data_home: Path, tmp_path: Path) -> None:
        _store(data_home, "s1", ["alpha beta gamma"])
        app = _App()
        await history_command.handle_history(app, '"alpha beta" --since 1d --cwd .')
        await history_command.handle_history(app, f"alpha --cwd {tmp_path / 'elsewhere'}")
        assert app.messages[0].startswith("s1  ") and app.messages[1] == "No matches."

    @pytest.mark.asyncio
    @pytest.mark.parametrize("args", ["", "--since", "word --since 3x", '"unbalanced'])
    async def test_a_malformed_command_reports_the_problem(self, args: str, data_home: Path) -> None:
        app = _App()
        await history_command.handle_history(app, args)
        assert app.messages[0].startswith("[red]")

    @pytest.mark.asyncio
    async def test_nothing_found_says_so(self, data_home: Path) -> None:
        app = _App()
        await history_command.handle_history(app, "nothing here")
        assert app.messages[0] == "No matches."


class TestCliHistory:
    def test_search_prints_session_date_role_and_snippet(self, data_home: Path) -> None:
        _store(data_home, "abc12345", ["where did the deploy script go"])
        result = CliRunner().invoke(cli, ["history", "search", "deploy script", "--since", "7d", "--cwd", "."])
        line   = result.output.strip()
        assert result.exit_code == 0 and line.startswith("abc12345  ") and "  user  " in line and "deploy script" in line

    def test_the_index_file_is_kept_under_the_data_root(self, data_home: Path) -> None:
        _store(data_home, "s1", ["findable"])
        CliRunner().invoke(cli, ["history", "search", "findable"])
        assert (data_home / "history-index.sqlite").exists() == fts5_available()

    def test_a_bad_window_is_a_usage_error(self, data_home: Path) -> None:
        result = CliRunner().invoke(cli, ["history", "search", "x", "--since", "soon"])
        assert result.exit_code == 2 and "--since" in result.output

    def test_limit_caps_the_lines(self, data_home: Path) -> None:
        _store(data_home, "s1", [f"note number {i}" for i in range(6)])
        result = CliRunner().invoke(cli, ["history", "search", "note", "--limit", "2"])
        assert len(result.output.strip().splitlines()) == 2


class TestCliContext:
    def test_a_stored_session_is_reported(self, data_home: Path) -> None:
        _store(data_home, "s1", ["explain the module", "and again"])
        result = CliRunner().invoke(cli, ["context", "s1"])
        assert result.exit_code == 0
        assert "Session s1" in result.output and "Rebuilt from a stored session" in result.output
        assert "System prompt" in result.output and "2 messages" in result.output

    def test_json_output_carries_totals_and_advice(self, data_home: Path) -> None:
        _store(data_home, "s1", ["explain the module"])
        data = json.loads(CliRunner().invoke(cli, ["context", "s1", "--json"]).output)
        assert data["session"] == "s1"
        assert data["total"] == data["system_tokens"] + data["tool_tokens"] + data["message_tokens"]
        assert isinstance(data["advice"], list)

    def test_without_an_id_the_latest_session_is_used(self, data_home: Path) -> None:
        _store(data_home, "latest1", ["hello"])
        assert "Session latest1" in CliRunner().invoke(cli, ["context"]).output

    @pytest.mark.parametrize("wanted", ["missing", "../escape"])
    def test_an_unknown_or_unsafe_id_is_an_error(self, data_home: Path, wanted: str) -> None:
        result = CliRunner().invoke(cli, ["context", wanted])
        assert result.exit_code == 2
