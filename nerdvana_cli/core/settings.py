"""Settings and configuration management."""

from __future__ import annotations

import os
from typing import Any

import yaml  # type: ignore[import-untyped]
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from nerdvana_cli.core import paths as core_paths


class ModelConfig(BaseModel):
    provider: str = ""  # empty = auto-detect from model name
    model: str = "claude-sonnet-4-20250514"
    api_key: str = ""
    base_url: str = ""
    max_tokens: int = 8192
    temperature: float = 1.0
    fallback_models: list[str] = Field(default_factory=list)
    extended_thinking: bool = False
    thinking_budget: int = 8192


class PermissionConfig(BaseModel):
    mode: str = "default"  # default, accept-edits, bypass, plan
    always_allow: list[str] = Field(default_factory=list)
    always_deny: list[str] = Field(default_factory=list)


class SessionConfig(BaseModel):
    persist: bool = True
    max_turns: int = 200
    max_context_tokens: int = 180_000
    compact_threshold: float = 0.8
    compact_max_failures: int = 3  # circuit breaker max consecutive failures
    planning_gate: bool = False  # enable complexity-triggered Plan agent before execution


class ParismConfig(BaseModel):
    enabled: bool = True
    config_path: str = ""
    format: str = "json"
    fallback_to_bash: bool = True


class HookConfig(BaseModel):
    session_start: list[str] = Field(default_factory=lambda: ["builtin:context_injection"])
    before_tool: list[str] = Field(default_factory=list)
    after_tool: list[str] = Field(default_factory=list)


class CheckpointConfig(BaseModel):
    enabled: bool = True
    per_session_max: int = 50


class NerdvanaSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="NERDVANA_", env_file=".env", extra="ignore")

    model: ModelConfig = Field(default_factory=ModelConfig)
    permissions: PermissionConfig = Field(default_factory=PermissionConfig)
    session: SessionConfig = Field(default_factory=SessionConfig)
    parism: ParismConfig = Field(default_factory=ParismConfig)
    hooks: HookConfig = Field(default_factory=HookConfig)
    checkpoint: CheckpointConfig = Field(default_factory=CheckpointConfig)
    cwd: str = "."
    verbose: bool = False
    config_path: str = ""

    @classmethod
    def load(cls, config_path: str | None = None) -> NerdvanaSettings:
        settings = cls()

        paths_to_check = [
            config_path,
            os.environ.get("NERDVANA_CONFIG", ""),
            os.path.join(os.getcwd(), "nerdvana.yml"),
            os.path.join(os.getcwd(), "nerdvana.yaml"),
            str(core_paths.user_config_path()),
            str(core_paths.legacy_config_path()),  # backwards compat
        ]

        for path in paths_to_check:
            if path and os.path.exists(path):
                with open(path) as f:
                    data = yaml.safe_load(f) or {}
                if "model" in data:
                    settings.model = ModelConfig(**data["model"])
                if "permissions" in data:
                    settings.permissions = PermissionConfig(**data["permissions"])
                if "session" in data:
                    settings.session = SessionConfig(**data["session"])
                if "parism" in data:
                    settings.parism = ParismConfig(**data["parism"])
                if "hooks" in data:
                    settings.hooks = HookConfig(**data["hooks"])
                if "checkpoint" in data:
                    settings.checkpoint = CheckpointConfig(**data["checkpoint"])
                settings.config_path = path
                break

        # Use canonical detect_provider from providers.base
        if not settings.model.provider:
            from nerdvana_cli.providers.base import detect_provider
            settings.model.provider = detect_provider(settings.model.model).value

        # Use canonical resolve_api_key from providers.factory
        if not settings.model.api_key:
            from nerdvana_cli.providers.base import ProviderName
            from nerdvana_cli.providers.factory import resolve_api_key
            try:
                prov = ProviderName(settings.model.provider)
                settings.model.api_key = resolve_api_key(prov)
            except ValueError:
                pass

        # Auto-apply model-specific context window
        user_set_context = False
        if settings.config_path and os.path.exists(settings.config_path):
            with open(settings.config_path) as f:
                raw = yaml.safe_load(f) or {}
            if "session" in raw and "max_context_tokens" in raw.get("session", {}):
                user_set_context = True

        if not user_set_context:
            from nerdvana_cli.providers.base import ProviderName, resolve_context_window
            try:
                prov = ProviderName(settings.model.provider)
                settings.session.max_context_tokens = resolve_context_window(prov, settings.model.model)
            except ValueError:
                pass

        return settings

    def to_api_params(self) -> dict[str, Any]:
        return {
            "model": self.model.model,
            "max_tokens": self.model.max_tokens,
            "temperature": self.model.temperature,
        }
