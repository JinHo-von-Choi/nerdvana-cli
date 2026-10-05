"""Self-update CLI wiring and git self-heal tests.

Covers the ``nerdvana update`` subcommand and the ``git pull`` fallback that
resets a clean install to ``origin/main`` when the fast-forward pull fails.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner

from nerdvana_cli.cli import updater
from nerdvana_cli.cli.updater import compare_versions, parse_version
from nerdvana_cli.main import app

runner = CliRunner()


def test_parse_version_basic():
    assert parse_version("v0.1.1") == (0, 1, 1)
    assert parse_version("0.1.1") == (0, 1, 1)
    assert parse_version("v1.2.3") == (1, 2, 3)


def test_parse_version_with_suffix():
    assert parse_version("v0.1.1-beta") == (0, 1, 1)
    assert parse_version("v1.0.0rc1") == (1, 0, 0)


def test_compare_newer():
    assert compare_versions("0.1.1", "0.1.2") == -1


def test_compare_same():
    assert compare_versions("0.1.1", "0.1.1") == 0


def test_compare_older():
    assert compare_versions("0.2.0", "0.1.1") == 1


def test_compare_major():
    assert compare_versions("0.9.9", "1.0.0") == -1


def test_parse_invalid():
    assert parse_version("invalid") == (0, 0, 0)


# ---------------------------------------------------------------------------
# nerdvana update subcommand
# ---------------------------------------------------------------------------

def test_update_command_reports_success() -> None:
    with patch.object(updater, "run_self_update", return_value=(True, "Updated successfully.")) as mocked:
        result = runner.invoke(app, ["--no-update-check", "update"])

    mocked.assert_called_once()
    assert result.exit_code == 0
    assert "Updated successfully." in result.output


def test_update_command_reports_failure_with_exit_1() -> None:
    with patch.object(updater, "run_self_update", return_value=(False, "git pull failed: boom")):
        result = runner.invoke(app, ["--no-update-check", "update"])

    assert result.exit_code == 1
    assert "git pull failed: boom" in result.output


# ---------------------------------------------------------------------------
# git pull self-heal
# ---------------------------------------------------------------------------

class _Completed:
    """Stand-in for ``subprocess.CompletedProcess``."""

    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _init_fake_install(install_dir: Path) -> None:
    """Create a clean git repo inside ``install_dir``."""
    install_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=install_dir, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=install_dir, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=install_dir, check=True)
    (install_dir / "README.md").write_text("install\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=install_dir, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=install_dir, check=True)


# Captured at import time: patching updater.subprocess.run patches the shared
# subprocess module, so the tests must hold the original for real git calls.
_REAL_SUBPROCESS_RUN = subprocess.run


def _real_run(cmd, **kwargs):
    return _REAL_SUBPROCESS_RUN(
        cmd,
        capture_output=True,
        text=True,
        cwd=kwargs.get("cwd"),
        timeout=kwargs.get("timeout"),
    )


def test_pull_failure_recovers_on_clean_install(tmp_path: Path) -> None:
    install = tmp_path / "install"
    _init_fake_install(install)

    def fake_run(cmd, **kwargs):
        if cmd[:2] == ["git", "pull"]:
            return _Completed(1, "", "fatal: refusing to fast-forward")
        if cmd[:2] == ["git", "status"]:
            return _real_run(cmd, **kwargs)
        return _Completed(0)

    with patch.object(updater.subprocess, "run", side_effect=fake_run):
        state, message = updater._git_pull(install)

    assert state == "updated"
    assert "git reset --hard origin/main" in message


def test_pull_failure_keeps_error_when_install_is_dirty(tmp_path: Path) -> None:
    install = tmp_path / "install"
    _init_fake_install(install)
    (install / "rogue.txt").write_text("unexpected\n", encoding="utf-8")

    def fake_run(cmd, **kwargs):
        if cmd[:2] == ["git", "pull"]:
            return _Completed(1, "", "fatal: pull failed")
        if cmd[:2] == ["git", "status"]:
            return _real_run(cmd, **kwargs)
        return _Completed(0)

    with patch.object(updater.subprocess, "run", side_effect=fake_run):
        state, message = updater._git_pull(install)

    assert state == "error"
    assert "git pull failed" in message
    assert "git reset" not in message


def test_pull_failure_keeps_error_when_recovery_fetch_fails(tmp_path: Path) -> None:
    install = tmp_path / "install"
    _init_fake_install(install)

    def fake_run(cmd, **kwargs):
        if cmd[:2] == ["git", "pull"]:
            return _Completed(1, "", "fatal: pull failed")
        if cmd[:2] == ["git", "fetch"]:
            return _Completed(1, "", "fatal: unable to access origin")
        if cmd[:2] == ["git", "status"]:
            return _real_run(cmd, **kwargs)
        return _Completed(0)

    with patch.object(updater.subprocess, "run", side_effect=fake_run):
        state, message = updater._git_pull(install)

    assert state == "error"
    assert "git pull failed" in message
    assert "git fetch origin main failed" in message


def test_run_self_update_continues_after_recovery(tmp_path: Path) -> None:
    """A recovered pull keeps going: pip install runs and the update succeeds."""
    install   = tmp_path / "install"
    data_home = tmp_path / "data"
    _init_fake_install(install)
    data_home.mkdir()
    (data_home / "config.yml").write_text("model:\n  provider: anthropic\n", encoding="utf-8")

    def fake_run(cmd, **kwargs):
        if cmd[:2] == ["git", "pull"]:
            return _Completed(1, "", "fatal: pull failed")
        if cmd[:2] == ["git", "status"]:
            return _real_run(cmd, **kwargs)
        if cmd[:2] in (["git", "fetch"], ["git", "reset"]):
            return _Completed(0)
        return _Completed(0, "ok", "")

    with patch.object(updater, "user_data_home", return_value=data_home), \
         patch.object(updater, "_run_post_update_migrate"), \
         patch.object(updater.subprocess, "run", side_effect=fake_run):
        ok, message = updater.run_self_update(install_dir=install)

    assert ok, message
    assert "git reset --hard origin/main" in message
    assert "Updated successfully" in message
    assert (data_home / "config.yml").read_text(encoding="utf-8").startswith("model:")
