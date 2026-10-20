"""``nerdvana --profile`` and ``--dump-config``: the settings of a run, and where they came from.

Author: 최진호
Date:   2026-10-21
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from nerdvana_cli.main import app

runner = CliRunner()


@pytest.fixture()
def env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("NERDVANA_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("NERDVANA_NO_UPDATE_CHECK", "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.delenv("NERDVANA_CONFIG", raising=False)
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_the_dump_prints_the_settings_and_the_layer_of_every_key(env: Path) -> None:
    result = runner.invoke(app, ["--profile", "bench-strict", "--dump-config"])

    assert result.exit_code == 0, result.output
    assert "# the settings this run uses" in result.output
    assert "session.persist: profile:bench-strict" in result.output
    assert "session.max_turns: base" in result.output


def test_the_command_line_patch_shows_up_in_the_dump(env: Path) -> None:
    result = runner.invoke(app, ["--patch", "session.max_turns=42", "--dump-config"])

    assert result.exit_code == 0, result.output
    assert "session.max_turns: cli_patch" in result.output


def test_the_dump_never_prints_the_api_key(env: Path) -> None:
    result = runner.invoke(app, ["--dump-config"])

    assert result.exit_code == 0, result.output
    assert "test-key" not in result.output
    assert "api_key: '[set]'" in result.output


def test_an_unknown_profile_ends_the_command_with_the_config_exit_code(env: Path) -> None:
    result = runner.invoke(app, ["--profile", "nope", "--dump-config"])

    assert result.exit_code == 2
    assert "Unknown profile" in result.output


def test_a_patch_that_may_not_loosen_the_rules_ends_the_command(env: Path) -> None:
    result = runner.invoke(app, ["--patch", "permissions.mode=bypass", "--dump-config"])

    assert result.exit_code == 2
    assert "may not change" in result.output
