"""Unit tests for central path resolution."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from nerdvana_cli.core import paths


@pytest.fixture
def fake_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("NERDVANA_DATA_HOME", raising=False)
    monkeypatch.delenv("NERDVANA_HOME", raising=False)
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    return tmp_path


def test_user_data_home_defaults_to_home_nerdvana(fake_home: Path) -> None:
    assert paths.user_data_home() == fake_home / ".nerdvana"


def test_user_data_home_respects_env_override(fake_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NERDVANA_DATA_HOME", str(fake_home / "custom"))
    assert paths.user_data_home() == fake_home / "custom"


def test_user_sessions_dir_is_under_data_home(fake_home: Path) -> None:
    assert paths.user_sessions_dir() == fake_home / ".nerdvana" / "sessions"


def test_user_config_path_is_config_yml(fake_home: Path) -> None:
    assert paths.user_config_path() == fake_home / ".nerdvana" / "config.yml"


def test_user_skills_dir(fake_home: Path) -> None:
    assert paths.user_skills_dir() == fake_home / ".nerdvana" / "skills"


def test_user_hooks_dir(fake_home: Path) -> None:
    assert paths.user_hooks_dir() == fake_home / ".nerdvana" / "hooks"


def test_user_agents_dir(fake_home: Path) -> None:
    assert paths.user_agents_dir() == fake_home / ".nerdvana" / "agents"


def test_user_teams_dir(fake_home: Path) -> None:
    assert paths.user_teams_dir() == fake_home / ".nerdvana" / "teams"


def test_user_mcp_json(fake_home: Path) -> None:
    assert paths.user_mcp_json() == fake_home / ".nerdvana" / "mcp.json"


def test_user_cache_dir(fake_home: Path) -> None:
    assert paths.user_cache_dir() == fake_home / ".nerdvana" / "cache"


def test_install_root_defaults_to_home_nerdvana_cli(fake_home: Path) -> None:
    assert paths.install_root() == fake_home / ".nerdvana-cli"


def test_install_root_respects_nerdvana_home_env(fake_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NERDVANA_HOME", str(fake_home / "elsewhere"))
    assert paths.install_root() == fake_home / "elsewhere"


def test_ensure_user_dirs_creates_all(fake_home: Path) -> None:
    paths.ensure_user_dirs()
    for sub in ("sessions", "skills", "hooks", "agents", "teams", "cache", "logs"):
        assert (fake_home / ".nerdvana" / sub).is_dir()


def test_project_skills_dir_takes_cwd(tmp_path: Path) -> None:
    assert paths.project_skills_dir(str(tmp_path)) == tmp_path / ".nerdvana" / "skills"


def test_legacy_xdg_config_detection(fake_home: Path) -> None:
    legacy = fake_home / ".config" / "nerdvana-cli" / "config.yml"
    legacy.parent.mkdir(parents=True)
    legacy.write_text("model: {}")
    # New location does not exist yet -> legacy_config_path should return the legacy one
    assert paths.legacy_config_path() == legacy
    # New location does NOT pre-empt legacy detection on its own
    assert paths.user_config_path() == fake_home / ".nerdvana" / "config.yml"
