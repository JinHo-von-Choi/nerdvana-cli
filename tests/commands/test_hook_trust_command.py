"""`nerdvana hook trust|revoke|trusted` behaviour.

The approval record lives under the data home, so every test points
NERDVANA_DATA_HOME at a temporary directory and reloads the modules that
resolved it at import time. Without that a run would write into the real
~/.nerdvana of whoever executes the suite.
"""
from __future__ import annotations

import importlib
from pathlib import Path

import pytest
from typer.testing import CliRunner


@pytest.fixture
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Point the data home at tmp_path and hand back the reloaded modules."""
    monkeypatch.setenv("NERDVANA_DATA_HOME", str(tmp_path / "data"))

    from nerdvana_cli.core import paths, user_hooks

    importlib.reload(paths)
    importlib.reload(user_hooks)

    from nerdvana_cli.commands import hook_command

    importlib.reload(hook_command)

    yield user_hooks, hook_command

    monkeypatch.delenv("NERDVANA_DATA_HOME", raising=False)
    importlib.reload(paths)
    importlib.reload(user_hooks)
    importlib.reload(hook_command)


@pytest.fixture
def hook_file(tmp_path: Path) -> Path:
    """A plausible project hook on disk."""
    hooks = tmp_path / "project" / ".nerdvana" / "hooks"
    hooks.mkdir(parents=True)
    target = hooks / "my_hook.py"
    target.write_text("def register(engine, settings):\n    pass\n", encoding="utf-8")
    return target


def test_trust_records_the_current_digest(isolated_home, hook_file: Path) -> None:
    user_hooks, hook_command = isolated_home
    result = CliRunner().invoke(hook_command.hook_app, ["trust", str(hook_file)])

    assert result.exit_code == 0
    record = user_hooks.load_trust_record()
    assert record[str(hook_file.resolve())] == user_hooks.hook_digest(hook_file)


def test_trust_refuses_a_path_that_is_not_a_file(isolated_home, tmp_path: Path) -> None:
    user_hooks, hook_command = isolated_home
    missing = tmp_path / "nope.py"
    result  = CliRunner().invoke(hook_command.hook_app, ["trust", str(missing)])

    assert result.exit_code == 1
    assert user_hooks.load_trust_record() == {}


def test_trust_survives_a_missing_record_directory(isolated_home, hook_file: Path) -> None:
    """The first approval creates the data home rather than failing."""
    user_hooks, hook_command = isolated_home
    assert not user_hooks.project_hook_trust_path().exists()

    result = CliRunner().invoke(hook_command.hook_app, ["trust", str(hook_file)])

    assert result.exit_code == 0
    assert user_hooks.project_hook_trust_path().is_file()


def test_revoke_drops_the_approval(isolated_home, hook_file: Path) -> None:
    user_hooks, hook_command = isolated_home
    runner = CliRunner()
    runner.invoke(hook_command.hook_app, ["trust", str(hook_file)])

    result = runner.invoke(hook_command.hook_app, ["revoke", str(hook_file)])

    assert result.exit_code == 0
    assert user_hooks.load_trust_record() == {}


def test_revoke_reports_when_nothing_was_approved(isolated_home, hook_file: Path) -> None:
    _, hook_command = isolated_home
    result = CliRunner().invoke(hook_command.hook_app, ["revoke", str(hook_file)])

    assert result.exit_code == 1


def test_trusted_lists_nothing_before_any_approval(isolated_home) -> None:
    _, hook_command = isolated_home
    result = CliRunner().invoke(hook_command.hook_app, ["trusted"])

    assert result.exit_code == 0
    assert "No approvals recorded" in result.output


def test_trusted_marks_an_unchanged_hook_current(isolated_home, hook_file: Path) -> None:
    _, hook_command = isolated_home
    runner = CliRunner()
    runner.invoke(hook_command.hook_app, ["trust", str(hook_file)])

    result = runner.invoke(hook_command.hook_app, ["trusted"])

    assert result.exit_code == 0
    assert "current" in result.output


def test_trusted_flags_a_hook_edited_after_approval(isolated_home, hook_file: Path) -> None:
    """This is the state the approval gate exists to catch."""
    _, hook_command = isolated_home
    runner = CliRunner()
    runner.invoke(hook_command.hook_app, ["trust", str(hook_file)])

    hook_file.write_text(
        "import os\nos.system('id')\n\n\ndef register(engine, settings):\n    pass\n",
        encoding="utf-8",
    )
    result = runner.invoke(hook_command.hook_app, ["trusted"])

    assert result.exit_code == 0
    assert "contents changed" in result.output


def test_trusted_flags_a_hook_that_disappeared(isolated_home, hook_file: Path) -> None:
    _, hook_command = isolated_home
    runner = CliRunner()
    runner.invoke(hook_command.hook_app, ["trust", str(hook_file)])
    hook_file.unlink()

    result = runner.invoke(hook_command.hook_app, ["trusted"])

    assert result.exit_code == 0
    assert "file is gone" in result.output


def test_approving_again_after_an_edit_restores_execution(
    isolated_home, hook_file: Path,
) -> None:
    """Re-approving is the documented way back, and it must actually work."""
    user_hooks, hook_command = isolated_home
    runner = CliRunner()
    runner.invoke(hook_command.hook_app, ["trust", str(hook_file)])

    hook_file.write_text("def register(engine, settings):\n    return None\n", encoding="utf-8")
    stale = user_hooks.load_trust_record()[str(hook_file.resolve())]
    assert stale != user_hooks.hook_digest(hook_file)

    runner.invoke(hook_command.hook_app, ["trust", str(hook_file)])

    fresh = user_hooks.load_trust_record()[str(hook_file.resolve())]
    assert fresh == user_hooks.hook_digest(hook_file)
