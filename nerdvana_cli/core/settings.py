"""Settings and configuration management."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Literal, TypeVar

import yaml  # type: ignore[import-untyped,unused-ignore]
from pydantic import BaseModel, Field, PrivateAttr, TypeAdapter, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

from nerdvana_cli.core import paths as core_paths

_SectionT = TypeVar("_SectionT", bound=BaseModel)


class SettingsLoadError(ValueError):
    """A security-relevant setting (permissions, trust, credentials) is invalid.

    These fields are never replaced by defaults: the CLI refuses to start so
    that a typo cannot silently widen or narrow what the agent may do.
    """


@dataclass(frozen=True)
class SettingsWarning:
    """One recoverable problem found while loading the config file.

    ``kind`` is ``invalid_value`` (replaced by the field default) or
    ``unknown_key`` (ignored, possibly written by a newer version).
    ``value_type`` is the type name only; values are never echoed because a
    rejected field can still hold a secret.
    """

    kind:       Literal["invalid_value", "unknown_key"]
    path:       str
    value_type: str
    reason:     str

    def format(self) -> str:
        """Return a single-line human readable description."""
        if self.kind == "unknown_key":
            return f"{self.path}: unknown key, possibly from a newer version (ignored)"
        return f"{self.path}: invalid {self.value_type} value, using default ({self.reason})"


class ModelConfig(BaseModel):
    provider: str = ""  # empty = auto-detect from model name
    model: str = "claude-sonnet-4-20250514"
    api_key: str = ""
    base_url: str = ""
    max_tokens: int = 8192
    temperature: float = 1.0
    fallback_models: list[str] = Field(default_factory=list)
    # Retries of the same model on a transient failure before moving to the
    # next fallback model.
    max_retries: int = 2
    extended_thinking: bool = False
    thinking_budget: int = 8192
    show_thinking: bool = True


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
    # Phase F: runtime profiles — default context and mode names
    default_context: str = "standalone"
    default_mode:    str = "interactive"
    show_activity:   bool = True
    update_check:    bool = True  # startup new-version check (cached, 24h TTL)
    # Provider stream limits in seconds; 0 disables. Idle is the longest gap
    # between two events, total the longest single response.
    stream_idle_timeout:  float = 300.0
    stream_total_timeout: float = 3600.0


class ParismConfig(BaseModel):
    enabled: bool = True
    config_path: str = ""
    format: str = "json"
    fallback_to_bash: bool = True


class HookConfig(BaseModel):
    session_start: list[str] = Field(default_factory=lambda: ["builtin:context_injection"])
    before_tool: list[str] = Field(default_factory=list)
    after_tool: list[str] = Field(default_factory=list)
    # Project-local hooks (<cwd>/.nerdvana/hooks/*.py) execute code carried
    # by the repository, so they stay off until the user opts in and
    # approves each file's digest. See core.user_hooks.
    allow_project_hooks: bool = False


class CheckpointConfig(BaseModel):
    enabled: bool = True
    per_session_max: int = 50


_TOP_LEVEL_KEYS = frozenset({
    "model", "permissions", "session", "parism", "hooks", "checkpoint", "skills",
    "model_history", "external_projects_enabled", "cwd", "verbose", "config_path",
})

# Fields that are never softened. The sentinel marks a section whose every
# field governs permissions.
_ALL_FIELDS_STRICT = frozenset({"*"})
_MODEL_STRICT_FIELDS = frozenset({"api_key"})
_HOOKS_STRICT_FIELDS = frozenset({"allow_project_hooks"})


def _strict_bool(path: str, value: object) -> bool:
    """Validate an opt-in gate flag; raise instead of guessing on bad input."""
    try:
        return TypeAdapter(bool).validate_python(value)
    except ValidationError as exc:
        raise SettingsLoadError(f"{path}: expected a boolean, got {type(value).__name__}") from exc


def _build_section(
    cls:      type[_SectionT],
    name:     str,
    raw:      object,
    warnings: list[SettingsWarning],
    strict:   frozenset[str] = frozenset(),
) -> _SectionT:
    """Validate one config section, recovering from bad fields with a warning.

    Unknown keys are dropped with an ``unknown_key`` warning. A field that
    fails validation is dropped (its default applies) with an
    ``invalid_value`` warning; a failure outside any single field resets the
    whole section. Fields named in ``strict`` (or every field when ``strict``
    is ``_ALL_FIELDS_STRICT``) raise ``SettingsLoadError`` instead.
    """
    everything = "*" in strict
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        if strict:
            raise SettingsLoadError(f"{name}: expected a mapping, got {type(raw).__name__}")
        warnings.append(SettingsWarning("invalid_value", name, type(raw).__name__, "expected a mapping"))
        return cls()

    fields: dict[str, Any] = {}
    for key, value in raw.items():
        if key in cls.model_fields:
            fields[str(key)] = value
        else:
            warnings.append(SettingsWarning("unknown_key", f"{name}.{key}", type(value).__name__, ""))

    for _ in range(len(fields) + 1):
        try:
            return cls(**fields)
        except ValidationError as exc:
            dropped = False
            for err in exc.errors():
                loc = err["loc"]
                field = str(loc[0]) if loc else ""
                value_type = type(fields.get(field)).__name__
                if everything or field in strict:
                    raise SettingsLoadError(f"{name}.{field}: {err['msg']} (got {value_type})") from exc
                if field in fields:
                    del fields[field]
                    dropped = True
                    warnings.append(SettingsWarning("invalid_value", f"{name}.{field}", value_type, err["msg"]))
            if not dropped:
                warnings.append(SettingsWarning("invalid_value", name, type(raw).__name__, "section reset to defaults"))
                return cls()
    return cls()


class SkillsConfig(BaseModel):
    # Also scan ~/.claude/skills and <cwd>/.claude/skills, one tier below
    # the matching nerdvana skill directories. See core.skills.
    include_claude_skills: bool = False


class NerdvanaSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="NERDVANA_", env_file=".env", extra="ignore")

    model: ModelConfig = Field(default_factory=ModelConfig)
    permissions: PermissionConfig = Field(default_factory=PermissionConfig)
    session: SessionConfig = Field(default_factory=SessionConfig)
    parism: ParismConfig = Field(default_factory=ParismConfig)
    hooks: HookConfig = Field(default_factory=HookConfig)
    checkpoint: CheckpointConfig = Field(default_factory=CheckpointConfig)
    skills: SkillsConfig = Field(default_factory=SkillsConfig)
    model_history: dict[str, str] = Field(default_factory=dict)
    # External project tools hand a registered directory to a read-capable
    # subprocess, so the whole family stays off until the user opts in.
    # See tools.external_project_tools.
    external_projects_enabled: bool = False
    cwd: str = "."
    verbose: bool = False
    config_path: str = ""
    _load_warnings: list[SettingsWarning] = PrivateAttr(default_factory=list)

    @property
    def load_warnings(self) -> list[SettingsWarning]:
        """Recoverable problems recorded by the last ``load`` call."""
        return self._load_warnings

    @classmethod
    def load(cls, config_path: str | None = None) -> NerdvanaSettings:
        try:
            settings = cls()
        except Exception:
            # env_file=".env" is resolved relative to the current working
            # directory, so an unrelated, malformed, or unreadable .env can
            # abort startup. Retry with dotenv loading disabled; real
            # environment variables (NERDVANA_*) still apply and a genuine
            # config error will re-raise here.
            settings = cls(_env_file=None)  # type: ignore[call-arg]

        paths_to_check = [
            config_path,
            os.environ.get("NERDVANA_CONFIG", ""),
            os.path.join(os.getcwd(), "nerdvana.yml"),
            os.path.join(os.getcwd(), "nerdvana.yaml"),
            str(core_paths.user_config_path()),
            str(core_paths.legacy_config_path()),  # backwards compat
        ]

        user_set_context = False
        for path in paths_to_check:
            if path and os.path.exists(path):
                with open(path) as f:
                    data = yaml.safe_load(f) or {}
                if not isinstance(data, dict):
                    raise SettingsLoadError(f"{path}: top level must be a mapping, got {type(data).__name__}")
                warnings = settings._load_warnings
                for key in data:
                    if key not in _TOP_LEVEL_KEYS:
                        warnings.append(SettingsWarning("unknown_key", str(key), type(data[key]).__name__, ""))
                if "model" in data:
                    settings.model = _build_section(ModelConfig, "model", data["model"], warnings, _MODEL_STRICT_FIELDS)
                if "permissions" in data:
                    settings.permissions = _build_section(
                        PermissionConfig, "permissions", data["permissions"], warnings, _ALL_FIELDS_STRICT,
                    )
                if "session" in data:
                    session_data = data["session"]
                    # Phase F: planning_gate=true → default_mode=planning (deprecated in 0.8.0)
                    if (
                        isinstance(session_data, dict)
                        and session_data.get("planning_gate")
                        and "default_mode" not in session_data
                    ):
                        session_data = {**session_data, "default_mode": "planning"}
                    settings.session = _build_section(SessionConfig, "session", session_data, warnings)
                    user_set_context = "max_context_tokens" in settings.session.model_fields_set
                if "parism" in data:
                    settings.parism = _build_section(ParismConfig, "parism", data["parism"], warnings)
                if "hooks" in data:
                    settings.hooks = _build_section(HookConfig, "hooks", data["hooks"], warnings, _HOOKS_STRICT_FIELDS)
                if "checkpoint" in data:
                    settings.checkpoint = _build_section(CheckpointConfig, "checkpoint", data["checkpoint"], warnings)
                if "skills" in data:
                    settings.skills = _build_section(SkillsConfig, "skills", data["skills"], warnings)
                if "external_projects_enabled" in data:
                    settings.external_projects_enabled = _strict_bool(
                        "external_projects_enabled", data["external_projects_enabled"],
                    )
                if "model_history" in data and isinstance(data["model_history"], dict):
                    settings.model_history = {
                        str(k): str(v) for k, v in data["model_history"].items()
                    }
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

        # Auto-apply model-specific context window (skipped when the file set it)
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
