"""Tests for nerdvana_cli.core.setup helpers and wizard flow."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
import yaml

from nerdvana_cli.core import paths, setup
from nerdvana_cli.core.setup import (
    get_config_path,
    has_config_file,
    has_valid_api_key,
    load_config,
    run_setup,
    save_config,
)
from nerdvana_cli.providers.base import DEFAULT_MODELS, PROVIDER_KEY_ENVVARS, ProviderName


@pytest.fixture
def clean_key_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove every provider API key from the environment."""
    for env_vars in PROVIDER_KEY_ENVVARS.values():
        for var in env_vars:
            monkeypatch.delenv(var, raising=False)


@pytest.fixture
def config_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    cfg = tmp_path / "config.yml"
    monkeypatch.setattr(paths, "user_config_path", lambda: cfg)
    return cfg


def test_get_config_path_follows_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = tmp_path / "config.yml"
    monkeypatch.setattr(paths, "user_config_path", lambda: cfg)
    assert get_config_path() == str(cfg)


def test_has_valid_api_key_true_when_key_set(
    clean_key_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    assert has_valid_api_key() is True


def test_has_valid_api_key_false_when_empty_string(
    clean_key_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "   ")
    assert has_valid_api_key() is False


def test_has_valid_api_key_false_when_no_keys(clean_key_env: None) -> None:
    assert has_valid_api_key() is False


def test_has_valid_api_key_ignores_local_providers(
    clean_key_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OLLAMA_API_KEY", "local-key")
    monkeypatch.setenv("VLLM_API_KEY", "local-key")
    assert has_valid_api_key() is False


def test_has_config_file_missing(config_path: Path) -> None:
    assert has_config_file() is False


def test_has_config_file_present(config_path: Path) -> None:
    config_path.write_text("model:\n  provider: openai\n")
    assert has_config_file() is True


def test_load_config_missing_file(config_path: Path) -> None:
    assert load_config() == {}


def test_load_config_empty_file(config_path: Path) -> None:
    config_path.write_text("")
    assert load_config() == {}


def test_load_config_reads_yaml(config_path: Path) -> None:
    config_path.write_text(yaml.dump({"model": {"provider": "groq"}}))
    assert load_config() == {"model": {"provider": "groq"}}


def test_save_config_creates_parent_dirs_and_chmod(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "dir" / "config.yml"
    saved = save_config({"model": {"provider": "openai"}}, path=str(target))
    assert saved == str(target)
    assert target.exists()
    mode = os.stat(target).st_mode & 0o777
    assert mode == 0o600


def test_save_config_roundtrip(config_path: Path) -> None:
    save_config({"model": {"provider": "mistral"}})
    assert load_config() == {"model": {"provider": "mistral"}}


def test_run_setup_skips_when_config_and_key_exist(
    config_path: Path, clean_key_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path.write_text("model:\n  provider: openai\n")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-existing")
    assert run_setup() is None


def test_run_setup_force_reruns_wizard(
    config_path: Path, clean_key_env: None
) -> None:
    config_path.write_text("model:\n  provider: openai\n")

    def fake_prompt(question: str, **kwargs: Any) -> str:
        if "provider" in question.lower():
            return "1"
        if "API key" in question:
            return "sk-wizard"
        if "Max tokens" in question:
            return "4096"
        return str(kwargs.get("default", ""))

    with (
        patch("nerdvana_cli.core.setup.Prompt.ask", side_effect=fake_prompt),
        patch("nerdvana_cli.core.setup.Confirm.ask", return_value=True),
    ):
        result = run_setup(force=True)

    assert result is not None
    assert result["model"]["provider"] == "anthropic"
    assert result["model"]["model"] == DEFAULT_MODELS[ProviderName.ANTHROPIC]
    assert result["model"]["max_tokens"] == 4096
    assert result["model"]["api_key"] == "sk-wizard"
    assert load_config()["model"]["provider"] == "anthropic"


def test_run_setup_custom_model_when_not_default(
    config_path: Path, clean_key_env: None
) -> None:
    def fake_prompt(question: str, **kwargs: Any) -> str:
        if "provider" in question.lower():
            return "2"
        if "API key" in question:
            return "sk-custom"
        if "model name" in question.lower():
            return "gpt-4o-mini"
        if "Max tokens" in question:
            return "8192"
        return str(kwargs.get("default", ""))

    confirm_values = iter([False, True])  # use default model? no; (remaining yes)

    with (
        patch("nerdvana_cli.core.setup.Prompt.ask", side_effect=fake_prompt),
        patch(
            "nerdvana_cli.core.setup.Confirm.ask",
            side_effect=lambda *a, **k: next(confirm_values, True),
        ),
    ):
        result = run_setup(force=True)

    assert result is not None
    assert result["model"]["provider"] == "openai"
    assert result["model"]["model"] == "gpt-4o-mini"


def test_run_setup_ollama_local_mode_needs_no_key(
    config_path: Path, clean_key_env: None
) -> None:
    def fake_prompt(question: str, **kwargs: Any) -> str:
        if "Select provider" in question:
            return "9"  # ollama
        if "Select mode" in question:
            return "1"  # local
        if "Max tokens" in question:
            return "8192"
        return str(kwargs.get("default", ""))

    with (
        patch("nerdvana_cli.core.setup.Prompt.ask", side_effect=fake_prompt),
        patch("nerdvana_cli.core.setup.Confirm.ask", return_value=True),
    ):
        result = run_setup(force=True)

    assert result is not None
    assert result["model"]["provider"] == "ollama"
    assert result["model"]["base_url"] == "http://localhost:11434/v1"
    assert result["model"]["api_key"] == ""


def test_run_setup_ollama_cloud_mode_uses_env_key(
    config_path: Path, clean_key_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OLLAMA_API_KEY", "cloud-key")

    def fake_prompt(question: str, **kwargs: Any) -> str:
        if "Select provider" in question:
            return "9"
        if "Select mode" in question:
            return "2"  # cloud
        if "Max tokens" in question:
            return "8192"
        return str(kwargs.get("default", ""))

    with (
        patch("nerdvana_cli.core.setup.Prompt.ask", side_effect=fake_prompt),
        patch("nerdvana_cli.core.setup.Confirm.ask", return_value=True),
    ):
        result = run_setup(force=True)

    assert result is not None
    assert result["model"]["base_url"] == "https://ollama.com/v1"
    assert result["model"]["api_key"] == "cloud-key"
    assert result["model"]["model"] == "gpt-oss:120b"


def test_build_providers_display_covers_all_enum_members() -> None:
    from nerdvana_cli.providers.base import ProviderName

    display = setup.build_providers_display()
    names = [row[0] for row in display]
    assert len(names) == len(set(names))
    assert set(names) == {p.value for p in ProviderName}
    assert len(display) == 21


def test_build_providers_display_preserves_legacy_indices() -> None:
    display = setup.build_providers_display()
    assert display[0][0] == "anthropic"
    assert display[1][0] == "openai"
    assert display[8][0] == "ollama"
    assert display[9][0] == "vllm"
    assert display[14][0] == "zai"
    assert display[15][0] == "moonshot"


def test_build_providers_display_pulls_defaults_from_base() -> None:
    from nerdvana_cli.providers.base import DEFAULT_MODELS, ProviderName

    display = setup.build_providers_display()
    by_name = {row[0]: row for row in display}
    assert by_name["anthropic"][2] == DEFAULT_MODELS[ProviderName.ANTHROPIC]
    assert by_name["cerebras"][2] == DEFAULT_MODELS[ProviderName.CEREBRAS]
    assert by_name["anthropic"][3] == "ANTHROPIC_API_KEY"
    assert by_name["ollama"][3] == "(no key needed)"
    assert by_name["vllm"][3] == "(no key needed)"


def test_build_providers_display_capability_annotation() -> None:
    display = setup.build_providers_display()
    by_name = {row[0]: row for row in display}
    caps = by_name["anthropic"][1]
    assert "tools" in caps
    assert "thinking" in caps
    assert "vision" in caps
    caps_groq = by_name["groq"][1]
    assert "tools" in caps_groq
    assert "vision" not in caps_groq
