"""Settings and configuration management."""

from __future__ import annotations

import os
from typing import Any

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class ModelConfig(BaseModel):
    provider: str = ""  # empty = auto-detect from model name
    model: str = "claude-sonnet-4-20250514"
    api_key: str = ""
    base_url: str = ""
    max_tokens: int = 8192
    temperature: float = 1.0


class PermissionConfig(BaseModel):
    mode: str = "default"  # default, accept-edits, bypass, plan
    always_allow: list[str] = Field(default_factory=list)
    always_deny: list[str] = Field(default_factory=list)


class SessionConfig(BaseModel):
    persist: bool = True
    max_turns: int = 200
    max_context_tokens: int = 180_000
    compact_threshold: float = 0.8


class ParismConfig(BaseModel):
    enabled: bool = True
    config_path: str = ""
    format: str = "json"
    fallback_to_bash: bool = True


class NerdvanaSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="NERDVANA_", env_file=".env", extra="ignore")

    model: ModelConfig = Field(default_factory=ModelConfig)
    permissions: PermissionConfig = Field(default_factory=PermissionConfig)
    session: SessionConfig = Field(default_factory=SessionConfig)
    parism: ParismConfig = Field(default_factory=ParismConfig)
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
            os.path.expanduser("~/.config/nerdvana-cli/config.yml"),
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

        return settings

    def to_api_params(self) -> dict[str, Any]:
        return {
            "model": self.model.model,
            "max_tokens": self.model.max_tokens,
            "temperature": self.model.temperature,
        }
