"""User-defined slash commands: discovery, rendering and dispatch.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from nerdvana_cli.core.user_commands import MAX_COMMAND_BYTES, UserCommand, UserCommandLoader
from nerdvana_cli.ui.app import NerdvanaApp
from nerdvana_cli.ui.command_dispatcher import dispatch_command


def _write(root: Path, name: str, text: str) -> Path:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


@pytest.fixture()
def dirs(tmp_path: Path) -> tuple[Path, Path, UserCommandLoader]:
    global_dir  = tmp_path / "global"
    project_dir = tmp_path / "proj"
    (project_dir / ".nerdvana" / "commands").mkdir(parents=True)
    global_dir.mkdir()
    return global_dir, project_dir / ".nerdvana" / "commands", UserCommandLoader(str(project_dir), str(global_dir))


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def test_files_become_commands_and_subdirectories_join_with_a_colon(dirs: Any) -> None:
    global_dir, project_cmds, loader = dirs
    _write(global_dir, "review.md", "Review the diff.")
    _write(project_cmds, "git/commit.md", "Write a commit message.")
    assert [c.name for c in loader.list_commands()] == ["git:commit", "review"]
    assert loader.get("/git:commit") is not None


def test_a_project_command_replaces_a_global_one_of_the_same_name(dirs: Any) -> None:
    global_dir, project_cmds, loader = dirs
    _write(global_dir, "fix.md", "global text")
    _write(project_cmds, "fix.md", "project text")
    assert loader.get("fix").body == "project text"  # type: ignore[union-attr]


def test_frontmatter_supplies_the_description_and_is_not_part_of_the_prompt(dirs: Any) -> None:
    global_dir, _, loader = dirs
    _write(global_dir, "ship.md", "---\ndescription: Ship it\n---\n\nDeploy to staging.\n")
    command = loader.get("ship")
    assert command is not None
    assert command.description == "Ship it"
    assert command.body == "Deploy to staging."


def test_lookup_ignores_case_and_the_slash(dirs: Any) -> None:
    global_dir, _, loader = dirs
    _write(global_dir, "Review.md", "text")
    assert loader.get("/REVIEW") is not None


def test_unusable_files_are_skipped(dirs: Any, tmp_path: Path) -> None:
    global_dir, _, loader = dirs
    _write(global_dir, "bad name.md", "text")
    _write(global_dir, "empty.md", "   \n")
    _write(global_dir, "big.md", "x" * (MAX_COMMAND_BYTES + 1))
    _write(global_dir, "ok.md", "fine")
    outside = _write(tmp_path / "elsewhere", "secret.md", "leaked")
    (global_dir / "link.md").symlink_to(outside)
    assert [c.name for c in loader.list_commands()] == ["ok"]


def test_missing_directories_are_fine(tmp_path: Path) -> None:
    assert UserCommandLoader(str(tmp_path / "none"), str(tmp_path / "also-none")).list_commands() == []


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def _command(body: str) -> UserCommand:
    return UserCommand(name="x", description="", body=body, source=Path("x.md"))


def test_arguments_placeholder_receives_everything_typed() -> None:
    assert _command("Fix: $ARGUMENTS now").render("the login bug") == "Fix: the login bug now"


def test_positional_placeholders_take_single_words_and_respect_quotes() -> None:
    assert _command("a=$1 b=$2 c=$3").render('one "two words"') == "a=one b=two words c="


def test_arguments_without_a_placeholder_are_appended() -> None:
    assert _command("Summarize the repository.").render("focus on tests") == "Summarize the repository.\n\nfocus on tests"


def test_no_arguments_leaves_the_template_alone() -> None:
    assert _command("Summarize the repository.").render("") == "Summarize the repository."
    assert _command("Do $ARGUMENTS").render("") == "Do "


def test_an_unbalanced_quote_falls_back_to_plain_words() -> None:
    assert _command("first=$1").render('"unclosed word') == 'first="unclosed'


def test_a_two_digit_reference_is_not_a_positional_placeholder() -> None:
    assert _command("cost $10 each").render("") == "cost $10 each"


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


class _Loop:
    class skill_loader:  # noqa: N801
        @staticmethod
        def get_by_trigger(_trigger: str) -> None:
            return None


class _FakeApp:
    def __init__(self, commands: list[UserCommand]) -> None:
        self._agent_loop = _Loop()
        self._commands   = commands
        self.started: list[tuple[str, str]] = []
        self.messages: list[str] = []

    def _user_commands(self) -> list[UserCommand]:
        return self._commands

    def _start_prompt(self, shown: str, prompt: str) -> None:
        self.started.append((shown, prompt))

    def _add_chat_message(self, markup: str, **_: Any) -> None:
        self.messages.append(markup)

    def exit(self) -> None:
        raise AssertionError("must not exit")


async def test_typing_a_user_command_sends_its_rendered_text() -> None:
    app = _FakeApp([UserCommand(name="review", description="", body="Review $ARGUMENTS", source=Path("r.md"))])
    await dispatch_command(app, "/review the parser")  # type: ignore[arg-type]
    assert app.started == [("/review the parser", "Review the parser")]
    assert app.messages == []


async def test_an_unknown_command_is_still_reported() -> None:
    app = _FakeApp([])
    await dispatch_command(app, "/nonsense")  # type: ignore[arg-type]
    assert app.started == []
    assert "Unknown command" in app.messages[0]


async def test_a_built_in_command_is_never_shadowed_by_a_file() -> None:
    from nerdvana_cli.ui import command_dispatcher

    handled: list[str] = []

    async def fake_handler(app: Any, args: str) -> None:
        handled.append(args)

    app = _FakeApp([UserCommand(name="help", description="", body="shadow", source=Path("h.md"))])
    original = command_dispatcher._build_handler_map
    command_dispatcher._build_handler_map = lambda: {"/help": fake_handler}  # type: ignore[assignment]
    try:
        await dispatch_command(app, "/help me")  # type: ignore[arg-type]
    finally:
        command_dispatcher._build_handler_map = original  # type: ignore[assignment]
    assert handled == ["me"]
    assert app.started == []


# ---------------------------------------------------------------------------
# App helpers
# ---------------------------------------------------------------------------


class _AppForPrompts:
    _start_prompt = NerdvanaApp._start_prompt

    def __init__(self, generating: bool) -> None:
        self._is_generating = generating
        self.queued:   list[str] = []
        self.sent:     list[str] = []
        self.messages: list[str] = []

        class _Loop:
            def queue_input(inner, text: str) -> None:  # noqa: N805
                self.queued.append(text)

        self._agent_loop = _Loop()

    def _add_chat_message(self, markup: str, **_: Any) -> None:
        self.messages.append(markup)

    def _generate_response(self, prompt: str) -> None:
        self.sent.append(prompt)


def test_an_idle_app_starts_a_turn_with_the_rendered_prompt() -> None:
    app = _AppForPrompts(generating=False)
    app._start_prompt("/review x", "Review x")
    assert app.sent == ["Review x"]
    assert app.queued == []


def test_a_busy_app_queues_the_rendered_prompt_instead() -> None:
    app = _AppForPrompts(generating=True)
    app._start_prompt("/review x", "Review x")
    assert app.queued == ["Review x"]
    assert app.sent == []


def test_what_the_user_typed_is_escaped_in_the_chat_line() -> None:
    app = _AppForPrompts(generating=False)
    app._start_prompt("/run [bold]x[/bold]", "p")
    assert "\\[bold]" in app.messages[0]
