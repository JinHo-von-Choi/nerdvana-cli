"""Partial recovery when loading the YAML config.

작성자: 최진호
작성일: 2026-10-03
"""

from __future__ import annotations

from pathlib import Path

import pytest

from nerdvana_cli.core.settings import NerdvanaSettings, SettingsLoadError


@pytest.fixture(autouse=True)
def _isolated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point HOME, data home, XDG and cwd at tmp_path so no real config is read."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("NERDVANA_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.delenv("NERDVANA_CONFIG", raising=False)
    monkeypatch.chdir(tmp_path)


def _write(tmp_path: Path, text: str) -> str:
    path = tmp_path / "nerdvana.yml"
    path.write_text(text, encoding="utf-8")
    return str(path)


class TestFieldRecovery:
    def test_invalid_field_falls_back_to_default_with_warning(self, tmp_path: Path) -> None:
        cfg = _write(tmp_path, "model:\n  max_tokens: lots\n  temperature: 0.2\n")
        settings = NerdvanaSettings.load(cfg)

        assert settings.model.max_tokens == 8192
        assert settings.model.temperature == 0.2
        [warning] = settings.load_warnings
        assert warning.kind == "invalid_value"
        assert warning.path == "model.max_tokens"
        assert warning.value_type == "str"
        assert warning.reason

    def test_several_bad_fields_in_one_section_all_recover(self, tmp_path: Path) -> None:
        cfg = _write(tmp_path, "session:\n  max_turns: [1]\n  compact_threshold: high\n  persist: false\n")
        settings = NerdvanaSettings.load(cfg)

        assert settings.session.max_turns == 200
        assert settings.session.compact_threshold == 0.8
        assert settings.session.persist is False
        assert {w.path for w in settings.load_warnings} == {"session.max_turns", "session.compact_threshold"}

    def test_section_that_is_not_a_mapping_resets_to_defaults(self, tmp_path: Path) -> None:
        cfg = _write(tmp_path, "checkpoint: nope\n")
        settings = NerdvanaSettings.load(cfg)

        assert settings.checkpoint.enabled is True
        [warning] = settings.load_warnings
        assert warning.path == "checkpoint"
        assert warning.value_type == "str"

    def test_empty_section_is_not_a_warning(self, tmp_path: Path) -> None:
        settings = NerdvanaSettings.load(_write(tmp_path, "session:\n"))
        assert settings.load_warnings == []

    def test_other_sections_survive_a_bad_one(self, tmp_path: Path) -> None:
        cfg = _write(tmp_path, "model:\n  max_tokens: x\nsession:\n  max_turns: 7\n")
        settings = NerdvanaSettings.load(cfg)
        assert settings.session.max_turns == 7

    def test_bad_value_is_never_echoed_in_the_warning(self, tmp_path: Path) -> None:
        cfg = _write(tmp_path, "model:\n  max_tokens: sk-secret-looking-value\n")
        settings = NerdvanaSettings.load(cfg)
        assert "sk-secret" not in settings.load_warnings[0].format()

    def test_user_set_context_survives_but_invalid_context_is_auto_resolved(self, tmp_path: Path) -> None:
        good = NerdvanaSettings.load(_write(tmp_path, "session:\n  max_context_tokens: 4321\n"))
        assert good.session.max_context_tokens == 4321

        bad = NerdvanaSettings.load(_write(tmp_path, "session:\n  max_context_tokens: many\n"))
        assert bad.session.max_context_tokens != 4321
        assert bad.session.max_context_tokens > 0


class TestUnknownKeys:
    def test_unknown_section_key_is_ignored_and_flagged(self, tmp_path: Path) -> None:
        cfg = _write(tmp_path, "model:\n  max_tokens: 100\n  telepathy: true\n")
        settings = NerdvanaSettings.load(cfg)

        assert settings.model.max_tokens == 100
        [warning] = settings.load_warnings
        assert warning.kind == "unknown_key"
        assert warning.path == "model.telepathy"
        assert "newer version" in warning.format()

    def test_unknown_top_level_key_is_flagged(self, tmp_path: Path) -> None:
        settings = NerdvanaSettings.load(_write(tmp_path, "future_section:\n  a: 1\n"))
        assert [(w.kind, w.path) for w in settings.load_warnings] == [("unknown_key", "future_section")]

    def test_clean_file_has_no_warnings(self, tmp_path: Path) -> None:
        settings = NerdvanaSettings.load(_write(tmp_path, "model:\n  max_tokens: 100\n"))
        assert settings.load_warnings == []


class TestStrictFields:
    @pytest.mark.parametrize(
        "body",
        [
            "permissions:\n  always_allow: Bash\n",
            "permissions:\n  always_deny: [1, [2]]\n",
            "permissions:\n  mode: [x]\n",
            "permissions: all\n",
        ],
    )
    def test_invalid_permissions_fail_loading(self, tmp_path: Path, body: str) -> None:
        with pytest.raises(SettingsLoadError, match="permissions"):
            NerdvanaSettings.load(_write(tmp_path, body))

    def test_valid_permissions_load(self, tmp_path: Path) -> None:
        settings = NerdvanaSettings.load(_write(tmp_path, "permissions:\n  always_allow: [Read]\n"))
        assert settings.permissions.always_allow == ["Read"]
        assert settings.load_warnings == []

    def test_unknown_permissions_key_warns_but_loads(self, tmp_path: Path) -> None:
        settings = NerdvanaSettings.load(_write(tmp_path, "permissions:\n  always_alow: [Bash]\n"))
        assert [w.path for w in settings.load_warnings] == ["permissions.always_alow"]

    def test_invalid_api_key_fails_loading(self, tmp_path: Path) -> None:
        with pytest.raises(SettingsLoadError, match=r"model\.api_key"):
            NerdvanaSettings.load(_write(tmp_path, "model:\n  api_key: [a, b]\n"))

    def test_invalid_project_hook_trust_flag_fails_loading(self, tmp_path: Path) -> None:
        with pytest.raises(SettingsLoadError, match="allow_project_hooks"):
            NerdvanaSettings.load(_write(tmp_path, "hooks:\n  allow_project_hooks: [yes]\n"))

    def test_other_hook_fields_still_recover(self, tmp_path: Path) -> None:
        settings = NerdvanaSettings.load(_write(tmp_path, "hooks:\n  before_tool: 3\n"))
        assert settings.hooks.before_tool == []
        assert [w.path for w in settings.load_warnings] == ["hooks.before_tool"]

    def test_invalid_external_projects_flag_fails_loading(self, tmp_path: Path) -> None:
        with pytest.raises(SettingsLoadError, match="external_projects_enabled"):
            NerdvanaSettings.load(_write(tmp_path, "external_projects_enabled: [1]\n"))

    def test_external_projects_flag_accepts_boolean(self, tmp_path: Path) -> None:
        settings = NerdvanaSettings.load(_write(tmp_path, "external_projects_enabled: true\n"))
        assert settings.external_projects_enabled is True


class TestStartupDisplay:
    def test_tui_shows_recovered_warnings_once_as_a_chat_message(self, tmp_path: Path) -> None:
        from types import SimpleNamespace

        from nerdvana_cli.ui.app import NerdvanaApp

        settings = NerdvanaSettings.load(_write(tmp_path, "model:\n  max_tokens: lots\n  telepathy: 1\n"))
        shown: list[str] = []
        stub = SimpleNamespace(settings=settings, _add_chat_message=shown.append)

        NerdvanaApp._show_load_warnings(stub)  # type: ignore[arg-type]

        assert len(shown) == 1
        assert "model.max_tokens" in shown[0]
        assert "model.telepathy" in shown[0]

    def test_tui_stays_silent_without_warnings(self, tmp_path: Path) -> None:
        from types import SimpleNamespace

        from nerdvana_cli.ui.app import NerdvanaApp

        shown: list[str] = []
        stub = SimpleNamespace(settings=NerdvanaSettings.load(_write(tmp_path, "")), _add_chat_message=shown.append)

        NerdvanaApp._show_load_warnings(stub)  # type: ignore[arg-type]

        assert shown == []
