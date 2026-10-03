"""Tests for AskUserTool: interactive answer, non-interactive guidance, registry scope."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Static

from nerdvana_cli.core.execution.tool_executor import ToolExecutor
from nerdvana_cli.core.tool import ToolContext
from nerdvana_cli.tools.ask_user_tool import AskUserArgs, AskUserTool
from nerdvana_cli.tools.registry import create_tool_registry
from nerdvana_cli.tools.subagent_registry import create_subagent_registry
from nerdvana_cli.ui.widgets import AskUserScreen


def _context_answering(answer: str | None, seen: list[tuple[str, list[str]]] | None = None) -> ToolContext:
    """Build a context whose ask_user hook records the prompt and replies *answer*."""

    async def _ask(question: str, options: list[str]) -> str | None:
        if seen is not None:
            seen.append((question, options))
        return answer

    return ToolContext(cwd=".", ask_user=_ask)


class TestInteractive:
    async def test_chosen_option_is_the_result(self) -> None:
        seen: list[tuple[str, list[str]]] = []
        ctx = _context_answering("PostgreSQL", seen)
        args = AskUserArgs(question="Which database?", options=["PostgreSQL", "SQLite"])

        result = await AskUserTool().call(args, ctx, can_use_tool=None)

        assert result.is_error is False
        assert result.content == "PostgreSQL"
        assert seen == [("Which database?", ["PostgreSQL", "SQLite"])]

    async def test_free_text_is_the_result(self) -> None:
        ctx = _context_answering("  use the existing Redis instance  ")
        args = AskUserArgs(question="Which database?", options=["PostgreSQL", "SQLite"])

        result = await AskUserTool().call(args, ctx, can_use_tool=None)

        assert result.is_error is False
        assert result.content == "use the existing Redis instance"

    async def test_options_are_optional(self) -> None:
        seen: list[tuple[str, list[str]]] = []
        ctx = _context_answering("blue", seen)

        result = await AskUserTool().call(AskUserArgs(question="Favourite colour?"), ctx, can_use_tool=None)

        assert result.content == "blue"
        assert seen == [("Favourite colour?", [])]

    @pytest.mark.parametrize("answer", [None, "", "   "])
    async def test_dismissed_prompt_is_an_error_with_guidance(self, answer: str | None) -> None:
        result = await AskUserTool().call(
            AskUserArgs(question="Proceed?", options=["yes", "no"]),
            _context_answering(answer),
            can_use_tool=None,
        )

        assert result.is_error is True
        assert "best judgment" in result.content


class TestNonInteractive:
    async def test_no_callback_returns_error_with_guidance(self) -> None:
        result = await AskUserTool().call(
            AskUserArgs(question="Which database?", options=["PostgreSQL", "SQLite"]),
            ToolContext(cwd="."),
            can_use_tool=None,
        )

        assert result.is_error is True
        assert "No user is available" in result.content
        assert "best judgment" in result.content
        assert "assumptions" in result.content

    async def test_executor_without_callback_reports_error(self) -> None:
        executor = ToolExecutor(
            registry = create_tool_registry(),
            hooks    = MagicMock(fire=MagicMock(return_value=[])),
            settings = None,
        )

        results = await executor.run_batch(
            [{"id": "t1", "name": "AskUser", "input": {"question": "Which?", "options": ["a", "b"]}}],
            ToolContext(cwd="."),
        )

        assert len(results) == 1
        assert results[0].tool_use_id == "t1"
        assert results[0].is_error is True
        assert "No user is available" in results[0].content

    async def test_executor_with_callback_returns_answer(self) -> None:
        executor = ToolExecutor(
            registry = create_tool_registry(),
            hooks    = MagicMock(fire=MagicMock(return_value=[])),
            settings = None,
        )

        results = await executor.run_batch(
            [{"id": "t1", "name": "AskUser", "input": {"question": "Which?", "options": ["a", "b"]}}],
            _context_answering("b"),
        )

        assert results[0].is_error is False
        assert results[0].content == "b"


class TestValidation:
    @pytest.mark.parametrize(
        ("question", "options"),
        [
            ("", []),
            ("   ", ["a", "b"]),
            ("Which?", ["a", "b", "c", "d", "e"]),
            ("Which?", ["a", ""]),
            ("Which?", ["a", 3]),
        ],
    )
    def test_invalid_input_is_rejected(self, question: str, options: list[Any]) -> None:
        error = AskUserTool().validate_input(AskUserArgs(question=question, options=options), ToolContext())

        assert error is not None

    @pytest.mark.parametrize("options", [[], ["a"], ["a", "b", "c", "d"]])
    def test_valid_input_is_accepted(self, options: list[str]) -> None:
        assert AskUserTool().validate_input(AskUserArgs(question="Which?", options=options), ToolContext()) is None

    def test_schema_requires_only_question(self) -> None:
        assert AskUserTool.input_schema["required"] == ["question"]
        assert AskUserTool.input_schema["properties"]["options"]["maxItems"] == 4


class TestRegistryScope:
    def test_present_in_main_registry(self) -> None:
        tool = create_tool_registry().get("AskUser")

        assert isinstance(tool, AskUserTool)

    def test_absent_from_default_subagent_registry(self) -> None:
        assert create_subagent_registry().get("AskUser") is None

    def test_absent_from_subagent_registry_even_when_allowed_by_name(self) -> None:
        registry = create_subagent_registry(allowed_tools=["AskUser", "Glob"])

        assert registry.get("AskUser") is None
        assert registry.get("Glob") is not None


class TestAskUserScreen:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("2", "beta"),
            (" 1 ", "alpha"),
            ("7", "7"),
            ("0", "0"),
            ("something else", "something else"),
            ("   ", None),
        ],
    )
    def test_resolve_answer(self, raw: str, expected: str | None) -> None:
        assert AskUserScreen.resolve_answer(raw, ["alpha", "beta"]) == expected

    async def test_typed_option_number_dismisses_with_option_text(self) -> None:
        answers: list[str | None] = []

        class _Host(App[None]):
            def compose(self) -> ComposeResult:
                yield Static("host")

            def on_mount(self) -> None:
                self.push_screen(AskUserScreen("Which?", ["alpha", "beta"]), answers.append)

        host = _Host()
        async with host.run_test() as pilot:
            await pilot.pause()
            host.screen.query_one("#ask-user-input").focus()
            await pilot.press("2", "enter")
            await pilot.pause()

        assert answers == ["beta"]

    async def test_escape_dismisses_with_none(self) -> None:
        answers: list[str | None] = []

        class _Host(App[None]):
            def compose(self) -> ComposeResult:
                yield Static("host")

            def on_mount(self) -> None:
                self.push_screen(AskUserScreen("Which?", []), answers.append)

        host = _Host()
        async with host.run_test() as pilot:
            await pilot.pause()
            await pilot.press("escape")
            await pilot.pause()

        assert answers == [None]
