"""How the settings of an ACP session are built: the launch options of ``nerdvana acp`` applied to the project's configuration.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass, field

from nerdvana_cli.cli.runtime import APPROVAL_MODE_MAP, resolve_run_provider
from nerdvana_cli.core.settings import NerdvanaSettings, SettingsLoadError, apply_settings_overrides


class LaunchError(Exception):
    """The session cannot start: the configuration is invalid or a required key is missing."""

    def __init__(self, message: str, *, auth_required: bool = False) -> None:
        super().__init__(message)
        self.auth_required = auth_required


@dataclass(frozen=True)
class LaunchOptions:
    """What the command line of ``nerdvana acp`` asked for; every session starts from it."""

    config_path:   str | None      = None
    model:         str             = ""
    provider:      str             = ""
    approval_mode: str             = ""
    set_values:    list[str]       = field(default_factory=list)


def settings_for(options: LaunchOptions, cwd: str) -> NerdvanaSettings:
    """The settings of a session that works in *cwd*.

    The project's ``nerdvana.yml`` is the one in *cwd*, not in the directory the agent was started from.
    Raises LaunchError when the configuration is invalid or the provider has no API key.
    """
    try:
        with contextlib.chdir(cwd):
            settings = NerdvanaSettings.load(options.config_path)
    except (SettingsLoadError, OSError) as exc:
        raise LaunchError(f"Invalid configuration: {exc}") from exc
    settings.cwd = cwd
    if options.model:
        settings.model.model = options.model
    if options.provider:
        settings.model.provider = options.provider
    if options.approval_mode:
        settings.session.default_mode = APPROVAL_MODE_MAP[options.approval_mode][0]
    try:
        apply_settings_overrides(settings, options.set_values)
    except ValueError as exc:
        raise LaunchError(str(exc)) from exc
    provider, key_missing = resolve_run_provider(settings)
    if key_missing:
        raise LaunchError(f"No API key found for {provider}.", auth_required=True)
    return settings
