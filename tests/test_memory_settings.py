"""The ``memory`` settings section.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

from pathlib import Path

import pytest

from nerdvana_cli.core.config.settings import NerdvanaSettings, SettingsLoadError, apply_settings_overrides
from nerdvana_cli.core.config.settings_sections import MemoryConfig


@pytest.fixture(autouse=True)
def _isolated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("NERDVANA_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.delenv("NERDVANA_CONFIG", raising=False)
    monkeypatch.chdir(tmp_path)


def _write(tmp_path: Path, text: str) -> str:
    path = tmp_path / "nerdvana.yml"
    path.write_text(text, encoding="utf-8")
    return str(path)


def test_review_is_off_by_default() -> None:
    assert MemoryConfig().review is False
    assert NerdvanaSettings.load().memory.review is False


def test_review_can_be_turned_on(tmp_path: Path) -> None:
    settings = NerdvanaSettings.load(_write(tmp_path, "memory:\n  review: true\n"))
    assert settings.memory.review is True
    assert settings.load_warnings == []


def test_unreadable_review_value_fails_loading_instead_of_turning_review_off(tmp_path: Path) -> None:
    with pytest.raises(SettingsLoadError, match="memory.review"):
        NerdvanaSettings.load(_write(tmp_path, "memory:\n  review: [maybe]\n"))


def test_unknown_memory_key_warns(tmp_path: Path) -> None:
    settings = NerdvanaSettings.load(_write(tmp_path, "memory:\n  inbox: elsewhere\n"))
    assert [w.path for w in settings.load_warnings] == ["memory.inbox"]
    assert settings.memory.review is False


def test_memory_is_a_known_top_level_key(tmp_path: Path) -> None:
    settings = NerdvanaSettings.load(_write(tmp_path, "memory:\n  review: false\n"))
    assert settings.load_warnings == []


def test_command_line_override_sets_review() -> None:
    settings = NerdvanaSettings.load()
    apply_settings_overrides(settings, ["memory.review=true"])
    assert settings.memory.review is True
