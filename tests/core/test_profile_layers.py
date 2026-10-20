"""Tests for the settings layers and their provenance (Task 3.2).

Author: 최진호
Date:   2026-10-21
"""

from __future__ import annotations

from pathlib import Path

import pytest

from nerdvana_cli.core.config.profile_layers import (
    LOCKED_SECTIONS,
    PROFILE_PRESETS,
    ProfileLayer,
    apply_layers,
    cli_layer,
    compose,
    dump_config,
    flatten_settings,
    patch_layer,
    profile_layer,
)
from nerdvana_cli.core.config.settings import NerdvanaSettings


def test_the_later_layer_wins_and_takes_the_provenance() -> None:
    layers = [
        ProfileLayer(name="base", values={"session.max_turns": 200}),
        ProfileLayer(name="profile:bench-strict", values={"session.max_turns": 50}),
        ProfileLayer(name="cli_patch", values={"session.max_turns": 10}),
    ]

    composed = compose(layers, base={"session.max_turns": 200, "model.provider": "openai"})

    assert composed.values["session.max_turns"] == 10
    assert composed.provenance["session.max_turns"] == "cli_patch"
    assert composed.provenance["model.provider"] == "base"


def test_the_base_keys_keep_the_base_provenance() -> None:
    composed = compose([], base={"model.provider": "openai"})

    assert composed.provenance == {"model.provider": "base"}
    assert composed.layered_keys() == []


def test_a_profile_carries_what_its_front_end_decides() -> None:
    layer = profile_layer("bench-strict")

    assert layer.name == "profile:bench-strict"
    assert layer.values == PROFILE_PRESETS["bench-strict"]
    assert layer.values["session.persist"] is False


def test_a_profile_never_touches_the_permission_mode() -> None:
    for name, values in PROFILE_PRESETS.items():
        assert not any(key.startswith("permissions.") for key in values), name


def test_an_unknown_profile_is_refused() -> None:
    with pytest.raises(ValueError, match="Unknown profile"):
        profile_layer("nope")


def test_a_patch_file_that_is_not_there_is_an_empty_layer(tmp_path: Path) -> None:
    layer = patch_layer(tmp_path / "nerdvana.patch.yml", "project_patch", locked=True)

    assert layer.values == {}


def test_a_patch_file_is_read_as_key_value_pairs(tmp_path: Path) -> None:
    patch = tmp_path / "nerdvana.patch.yml"
    patch.write_text("session:\n  max_turns: 7\n", encoding="utf-8")

    layer = patch_layer(patch, "project_patch", locked=True)

    assert layer.values == {"session": {"max_turns": 7}}


def test_the_command_line_patch_reads_its_value_as_yaml() -> None:
    layer = cli_layer(["session.max_turns=7", "telemetry.enabled=false"])

    assert layer.values == {"session.max_turns": 7, "telemetry.enabled": False}


def test_a_command_line_patch_without_a_value_is_refused() -> None:
    with pytest.raises(ValueError, match="section.field=value"):
        cli_layer(["session.max_turns"])


def test_a_project_patch_may_not_loosen_the_rules() -> None:
    layers = [ProfileLayer(name="project_patch", values={"permissions.mode": "bypass"}, locked_sections=LOCKED_SECTIONS)]

    with pytest.raises(ValueError, match="may not change"):
        compose(layers)


def test_the_command_line_may_not_loosen_the_rules() -> None:
    with pytest.raises(ValueError, match="may not change"):
        compose([cli_layer(["permissions.mode=bypass"])])


def test_the_layers_are_applied_to_the_settings() -> None:
    settings = NerdvanaSettings()
    composed = compose(
        [profile_layer("bench-strict"), cli_layer(["session.max_turns=42"])],
        base=flatten_settings(settings),
    )

    apply_layers(settings, composed)

    assert settings.session.persist is False
    assert settings.session.max_turns == 42
    assert settings.checkpoint.enabled is False


def test_a_profile_may_set_what_a_patch_may_not() -> None:
    settings = NerdvanaSettings()
    composed = compose(
        [ProfileLayer(name="profile:serve", values={"sandbox.mode": "require"})],
        base=flatten_settings(settings),
    )

    apply_layers(settings, composed)

    assert settings.sandbox.mode == "require"


def test_the_dump_shows_the_settings_and_where_every_key_came_from() -> None:
    settings = NerdvanaSettings()
    composed = compose(
        [profile_layer("bench-strict"), cli_layer(["session.max_turns=42"])],
        base=flatten_settings(settings),
    )

    dump = dump_config(settings, composed)

    assert "# the settings this run uses" in dump
    assert "# where every key came from" in dump
    assert "session.max_turns: cli_patch" in dump
    assert "session.persist: profile:bench-strict" in dump
    assert "model.provider: base" in dump


def test_the_dump_never_carries_a_credential() -> None:
    settings = NerdvanaSettings()
    settings.model.api_key = "sk-secret-value"
    settings.secrets.proxy_credentials = {"api.example.com": "token-secret-value"}
    composed = compose([], base=flatten_settings(settings))

    dump = dump_config(settings, composed)

    assert "sk-secret-value" not in dump
    assert "token-secret-value" not in dump
    assert "api_key: '[set]'" in dump


def test_the_dump_of_a_credential_free_setting_is_unchanged() -> None:
    settings = NerdvanaSettings()

    dump = dump_config(settings, compose([], base=flatten_settings(settings)))

    assert "api_key: ''" in dump
