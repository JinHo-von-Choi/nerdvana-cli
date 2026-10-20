"""The settings of a run, layer by layer, and where each key came from.

Task 3.2 of the Harness2/DSH enhancement plan.

A run's settings are not one file. They are the base configuration, the profile
of the front end that started it, the patches the user and the project wrote,
and the overlay the command line carries — applied in that order, each layer
overriding the one before it.

The composer keeps the provenance of every key: which layer decided it. A
setting that surprises someone is a question about its origin, and the answer
is one lookup away.

Based on DeepSeek Harness (``docs/architecture.md``, "Profiles and bundles").

Author: 최진호
Date:   2026-10-21
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped,unused-ignore]

from nerdvana_cli.core.config.paths import user_data_home
from nerdvana_cli.core.config.settings import NerdvanaSettings, assign_setting

# The patch file a home or a project carries.
PATCH_FILE_NAME = "nerdvana.patch.yml"

# The sections that decide what the agent may do. A layer that does not speak
# for the user may not change them.
LOCKED_SECTIONS: frozenset[str] = frozenset({"permissions", "hooks", "sandbox", "secrets"})

# The front ends the profiles name, and what each of them decides. A profile
# never touches the permission mode: that is the user's decision, not the
# front end's.
PROFILE_PRESETS: dict[str, dict[str, Any]] = {
    # The interactive terminal: the user is there to answer, and the session is kept.
    "tui": {"session.persist": True},
    # A run in a pipeline: the same rules, but the run leaves no session behind.
    "headless": {"session.persist": False},
    # The Agent Client Protocol server: another agent drives the run, and it is kept.
    "acp": {"session.persist": True},
    # The tool server: the calls come from outside, so the shell is confined.
    "serve": {"sandbox.mode": "require"},
    # A benchmark: nothing is written outside the run, and the run leaves nothing behind.
    "bench-strict": {
        "checkpoint.enabled": False,
        "goal.auto_verify":   False,
        "session.persist":    False,
    },
}


@dataclass(frozen=True)
class ProfileLayer:
    """One layer of the settings: its name, the keys it holds and the sections it may not change."""

    name:           str
    values:         dict[str, Any]
    locked_sections: frozenset[str] = frozenset()

    def locks(self, key: str) -> bool:
        """True when this layer may not change *key*."""
        return key.partition(".")[0] in self.locked_sections


@dataclass(frozen=True)
class ComposedSettings:
    """The settings the layers make, and the layer each key came from."""

    values:     dict[str, Any]
    provenance: dict[str, str]
    layers:     tuple[ProfileLayer, ...] = ()

    def layered_keys(self) -> list[str]:
        """The keys a layer set, in the order they were set."""
        return [key for key, layer in self.provenance.items() if layer != "base"]

    def layer_of(self, key: str) -> ProfileLayer | None:
        """The layer that set *key*, if a layer did."""
        name = self.provenance.get(key)
        return next((layer for layer in self.layers if layer.name == name), None)


def compose(layers: Sequence[ProfileLayer], base: Mapping[str, Any] | None = None) -> ComposedSettings:
    """The layers in order: the later one overrides the earlier, and it takes the provenance.

    Time complexity: O(L * K) for L layers and K keys.
    """
    values:     dict[str, Any] = dict(base or {})
    provenance: dict[str, str] = dict.fromkeys(values, "base")
    for layer in layers:
        for key, value in layer.values.items():
            if layer.locks(key):
                raise ValueError(f"Layer '{layer.name}' may not change '{key}'")
            values[key] = value
            provenance[key] = layer.name
    return ComposedSettings(values=values, provenance=provenance, layers=tuple(layers))


def apply_layers(settings: NerdvanaSettings, composed: ComposedSettings) -> NerdvanaSettings:
    """Apply the layered keys to *settings*, one key at a time.

    A key is set with the rules of the layer that set it: a built-in profile
    may change what a patch may not.
    """
    for key in composed.layered_keys():
        layer  = composed.layer_of(key)
        locked = layer.locked_sections if layer is not None else LOCKED_SECTIONS
        assign_setting(settings, key, composed.values[key], locked_sections=locked)
    return settings


def profile_layer(profile: str) -> ProfileLayer:
    """The layer of a named profile."""
    if profile not in PROFILE_PRESETS:
        known = ", ".join(sorted(PROFILE_PRESETS))
        raise ValueError(f"Unknown profile '{profile}'; known profiles: {known}")
    return ProfileLayer(name=f"profile:{profile}", values=dict(PROFILE_PRESETS[profile]))


def patch_layer(path: Path, name: str, locked: bool) -> ProfileLayer:
    """The layer of a patch file; a file that is not there is an empty layer."""
    values: dict[str, Any] = {}
    if path.is_file():
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(data, dict):
            raise ValueError(f"{path}: top level must be a mapping")
        values = {str(key): value for key, value in data.items()}
    return ProfileLayer(
        name            = name,
        values          = values,
        locked_sections = LOCKED_SECTIONS if locked else frozenset(),
    )


def cli_layer(patches: Sequence[str]) -> ProfileLayer:
    """The layer of the command line patches: ``section.field=value``, the value read as YAML."""
    values: dict[str, Any] = {}
    for patch in patches:
        path, sep, raw = patch.partition("=")
        if not sep:
            raise ValueError(f"--patch expects section.field=value, got '{patch}'")
        values[path.strip()] = yaml.safe_load(raw)
    return ProfileLayer(name="cli_patch", values=values, locked_sections=LOCKED_SECTIONS)


def settings_tree(settings: NerdvanaSettings) -> dict[str, Any]:
    """The settings as a tree of sections."""
    return {
        key: value
        for key, value in settings.model_dump(mode="json").items()
        if not key.startswith("_")
    }


# The fields whose value is a credential. A dump says that such a field is set,
# never what it holds: the dump travels to terminals, logs and bug reports.
SECRET_FIELD_NAMES: frozenset[str] = frozenset({
    "api_key",
    "api_keys",
    "credentials",
    "password",
    "proxy_credentials",
    "secret",
    "token",
})

# What a masked credential reads as.
SET_MARKER = "[set]"


def masked_tree(tree: dict[str, Any]) -> dict[str, Any]:
    """The settings tree with every credential field replaced by a note that it is set."""
    masked: dict[str, Any] = {}
    for key, value in tree.items():
        if value and key.lower() in SECRET_FIELD_NAMES:
            masked[key] = SET_MARKER
        elif isinstance(value, dict):
            masked[key] = masked_tree(value)
        else:
            masked[key] = value
    return masked


def flatten_settings(settings: NerdvanaSettings) -> dict[str, Any]:
    """The settings as dotted keys, the shape the layers speak."""
    flat: dict[str, Any] = {}
    for section, value in settings_tree(settings).items():
        if isinstance(value, dict):
            for field, field_value in value.items():
                flat[f"{section}.{field}"] = field_value
        else:
            flat[section] = value
    return flat


def dump_config(settings: NerdvanaSettings, composed: ComposedSettings) -> str:
    """The settings this run will use, and the layer every key came from."""
    tree = yaml.safe_dump(masked_tree(settings_tree(settings)), sort_keys=True, allow_unicode=True)
    lines = ["# the settings this run uses", tree.rstrip(), "", "# where every key came from"]
    for key in sorted(composed.provenance):
        lines.append(f"{key}: {composed.provenance[key]}")
    return "\n".join(lines) + "\n"


def load_settings_with_layers(
    config_path: str = "",
    profile:     str = "",
    patches:     Sequence[str] = (),
    cwd:         str = "",
) -> tuple[NerdvanaSettings, ComposedSettings]:
    """The settings of a run with every layer applied, and where every key came from.

    The layers are applied in order: the base configuration, the profile of the
    front end, the patch of the user's home, the patch of the project and the
    command line overlay. The last one wins.

    A patch of the project and the command line may not change the sections
    that decide what the agent may do: a repository and a command line do not
    loosen the rules of the run they start.
    """
    settings = NerdvanaSettings.load(config_path or None)
    root     = Path(cwd or settings.cwd or ".")
    layers   = [profile_layer(profile)] if profile else []
    layers.append(patch_layer(user_data_home() / PATCH_FILE_NAME, "home_patch", locked=False))
    layers.append(patch_layer(root / ".nerdvana" / PATCH_FILE_NAME, "project_patch", locked=True))
    layers.append(cli_layer(patches))
    composed = compose(layers, base=flatten_settings(settings))
    apply_layers(settings, composed)
    return settings, composed
