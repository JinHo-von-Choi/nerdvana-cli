"""Every global-path consumer must route through paths.user_*."""
from __future__ import annotations

from pathlib import Path

import pytest

from nerdvana_cli.core import nirnamd, paths, setup, user_hooks
from nerdvana_cli.core.settings import NerdvanaSettings
from nerdvana_cli.core.skills import SkillLoader
from nerdvana_cli.mcp.config import load_mcp_config


def test_setup_config_path_uses_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("NERDVANA_DATA_HOME", raising=False)
    assert setup.get_config_path() == str(paths.user_config_path())


def test_user_hooks_global_dir_uses_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("NERDVANA_DATA_HOME", raising=False)
    assert user_hooks.global_hooks_dir() == paths.user_hooks_dir()


def test_skill_loader_default_global_dir_uses_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("NERDVANA_DATA_HOME", raising=False)
    loader = SkillLoader(project_dir=str(tmp_path))
    assert loader._global_dir == paths.user_skills_dir()


def test_nirnamd_global_path_uses_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("NERDVANA_DATA_HOME", raising=False)
    assert Path(nirnamd.global_nirnamd_path()) == paths.user_nirnamd_path()


def test_settings_load_finds_user_data_home_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("NERDVANA_DATA_HOME", raising=False)
    monkeypatch.delenv("NERDVANA_CONFIG", raising=False)

    cfg_path = tmp_path / ".nerdvana" / "config.yml"
    cfg_path.parent.mkdir(parents=True)
    cfg_path.write_text("model:\n  model: test-model\n")

    settings = NerdvanaSettings.load()
    assert settings.model.model == "test-model"
    assert settings.config_path == str(cfg_path)
