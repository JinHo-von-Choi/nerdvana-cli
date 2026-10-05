"""Launch settings of an ACP session: the managed policy is enforced over every option.

Author: 최진호
Date:   2026-10-05
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("acp")

from nerdvana_cli.acp.launch import LaunchError, LaunchOptions, settings_for
from nerdvana_cli.core.config import paths as core_paths
from tests.acp.support import isolate


@pytest.fixture
def managed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An isolated managed directory, named by NERDVANA_MANAGED_DIR; the system directory is an empty temp one."""
    directory = tmp_path / "managed"
    directory.mkdir()
    monkeypatch.setattr(core_paths, "system_managed_dir", lambda: tmp_path / "system")
    monkeypatch.setenv("NERDVANA_MANAGED_DIR", str(directory))
    return directory


def test_settings_for_refuses_a_model_the_managed_policy_denies(
    tmp_path: Path, managed: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    (managed / "10-m.yml").write_text("model:\n  allowed_models: ['claude-*']\n", encoding="utf-8")
    isolate(monkeypatch, tmp_path)
    with pytest.raises(LaunchError) as caught:
        settings_for(LaunchOptions(model="gpt-4o"), str(tmp_path))
    assert not caught.value.auth_required
    assert "gpt-4o" in str(caught.value)
    assert "managed policy" in str(caught.value)


def test_settings_for_refuses_a_denied_model_named_by_an_override(
    tmp_path: Path, managed: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    (managed / "10-m.yml").write_text("model:\n  denied_models: ['gpt-*']\n", encoding="utf-8")
    isolate(monkeypatch, tmp_path)
    with pytest.raises(LaunchError) as caught:
        settings_for(LaunchOptions(set_values=["model.model=gpt-4o-mini"]), str(tmp_path))
    assert not caught.value.auth_required
    assert "gpt-4o-mini" in str(caught.value)


def test_settings_for_keeps_the_controls_when_the_model_is_inside_the_policy(
    tmp_path: Path, managed: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    (managed / "10-m.yml").write_text(
        "model:\n  allowed_models: ['claude-*']\nsession:\n  max_cost_usd: 4\n", encoding="utf-8",
    )
    isolate(monkeypatch, tmp_path)
    settings = settings_for(
        LaunchOptions(model="claude-sonnet-5-5", set_values=["session.max_cost_usd=9"]), str(tmp_path),
    )
    assert settings.model.model == "claude-sonnet-5-5"
    assert settings.session.max_cost_usd == 4
    assert settings.managed_policy.active
