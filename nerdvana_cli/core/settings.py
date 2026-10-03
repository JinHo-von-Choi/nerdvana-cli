"""Settings and configuration management."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, TypeVar

import yaml  # type: ignore[import-untyped,unused-ignore]
from pydantic import BaseModel, Field, PrivateAttr, TypeAdapter, ValidationError, ValidationInfo, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from nerdvana_cli.core import paths as core_paths
from nerdvana_cli.core.egress_proxy import normalize_pattern
from nerdvana_cli.core.secrets import ProxyCredential
from nerdvana_cli.core.settings_sections import (
    AgentsConfig,
    GoalConfig,
    MemoryConfig,
    SkillsConfig,
    TelemetryConfig,
    ToolsConfig,
    WorkflowConfig,
)

if TYPE_CHECKING:
    from nerdvana_cli.core.managed_policy import ManagedPolicy

_SectionT = TypeVar("_SectionT", bound=BaseModel)


class SettingsLoadError(ValueError):
    """A security-relevant setting (permissions, trust, credentials) is invalid.

    These fields are never replaced by defaults: the CLI refuses to start so
    that a typo cannot silently widen or narrow what the agent may do.
    """


@dataclass(frozen=True)
class SettingsWarning:
    """One recoverable problem found while loading the config file.

    ``kind`` is ``invalid_value`` (replaced by the field default),
    ``unknown_key`` (ignored, possibly written by a newer version) or
    ``removed_key`` (ignored, written for an older version) or
    ``user_only_key`` (ignored because a project file may not set it).
    ``value_type`` is the type name only; values are never echoed because a
    rejected field can still hold a secret.
    """

    kind:       Literal["invalid_value", "unknown_key", "removed_key", "user_only_key"]
    path:       str
    value_type: str
    reason:     str

    def format(self) -> str:
        """Return a single-line human readable description."""
        if self.kind == "removed_key":
            return f"{self.path}: no longer used (ignored); it can be deleted"
        if self.kind == "unknown_key":
            return f"{self.path}: unknown key, possibly from a newer version (ignored)"
        if self.kind == "user_only_key":
            return f"{self.path}: ignored in a project file; set it in your user config or with --config"
        return f"{self.path}: invalid {self.value_type} value, using default ({self.reason})"


class ModelConfig(BaseModel):
    provider: str = ""  # empty = auto-detect from model name
    model: str = "claude-sonnet-5-5"
    api_key: str = ""
    base_url: str = ""
    max_tokens: int = 8192
    temperature: float = 1.0
    fallback_models: list[str] = Field(default_factory=list)
    # Retries of the same model on a transient failure before moving to the
    # next fallback model.
    max_retries: int = 2
    # Let the provider reuse the processed system prompt, tool list and conversation
    # prefix between requests (cached reads cost a fraction of normal input).
    prompt_caching: bool = True
    extended_thinking: bool = False
    thinking_budget: int = 8192
    show_thinking: bool = True
    # How hard models reason, in the provider's own words (Anthropic ``output_config.effort``,
    # OpenAI ``reasoning_effort``, Gemini ``thinking_level``). Empty keeps the provider default.
    reasoning_effort: str = ""
    # Reasoning effort per phase of a run, in the same words as reasoning_effort: the plan agent drafts at
    # effort_planning, the main run works at effort_implementation, and the turns after a failed goal
    # verification run at effort_verification. Empty = reasoning_effort for that phase.
    effort_planning:       str = ""
    effort_implementation: str = ""
    effort_verification:   str = ""
    # Which OpenAI API OpenAI-compatible providers use: "auto" speaks Responses to OpenAI's own endpoint and
    # Chat Completions to every other server (Groq, Ollama, OpenRouter, ...); "chat" and "responses" force one.
    openai_api: Literal["auto", "chat", "responses"] = "auto"
    # Which Gemini API the gemini provider uses: "generate_content" (the default until the Interactions path is
    # verified against the live API), "interactions" (stateless, ``store: false``), or "auto" (picks Interactions).
    gemini_api: Literal["auto", "generate_content", "interactions"] = "generate_content"
    # Anthropic only. Server-side tool search: MCP tools are declared with defer_loading plus Anthropic's BM25 or
    # regex search tool, so the model discovers them without the local ToolSearch tool. "off" keeps every
    # declaration as it is.
    anthropic_tool_search: Literal["off", "bm25", "regex"] = "off"
    # Anthropic only. Server-side compaction (beta): the provider can ask the API to summarize the conversation
    # and sends the signed summary back in place of the messages it replaced. core/compact.py stays the
    # fallback for every other provider and for a model that does not support it.
    anthropic_compaction: Literal["off", "on"] = "off"
    # Anthropic only. Declares Anthropic's client-side memory tool; the files live in a per-project directory of
    # the data root.
    anthropic_memory_tool: bool = False

    @field_validator("anthropic_tool_search", "anthropic_compaction", mode="before")
    @classmethod
    def _unquoted_yaml_switches(cls, value: Any, info: ValidationInfo) -> Any:
        """YAML reads an unquoted ``off`` as False and ``on`` as True; take them as the words they were written as."""
        if value is False:
            return "off"
        return "on" if value is True and info.field_name == "anthropic_compaction" else value


class PermissionConfig(BaseModel):
    mode: str = "default"  # default, accept-edits, bypass, plan
    always_allow: list[str] = Field(default_factory=list)
    always_deny: list[str] = Field(default_factory=list)
    # Ask before a Bash command or a state-changing MCP call that repeats text returned by the web or an MCP server.
    gate_untrusted_sources: bool = True
    # A second model call that judges each call the policy would run without asking (core/classifier.py).
    # "shadow" records what it would have done next to the real decision and changes nothing; "enforce"
    # turns an allow into an ask or a refusal. It never relaxes an ask or a refusal.
    classifier: Literal["off", "shadow", "enforce"] = "off"
    # Model for the classifier, "model" or "provider:model"; empty = the agents.categories entry "classifier",
    # then "quick", then the session model.
    classifier_model: str = ""

    @field_validator("classifier", mode="before")
    @classmethod
    def _unquoted_yaml_off(cls, value: Any) -> Any:
        """YAML reads an unquoted ``off`` as False; take it as the word it was written as."""
        return "off" if value is False else value


class SessionConfig(BaseModel):
    persist: bool = True
    max_turns: int = 200
    # Stop once the estimated cost of this session's provider requests reaches this
    # many USD; 0 means no limit. Needs a known price for the model.
    max_cost_usd: float = 0.0
    # Total tokens (input + output, every request) after which the run stops; 0 = no limit.
    # A limit that needs no price list, for models the price table does not know.
    max_total_tokens: int = 0
    # Longest a project document (NIRNA.md, AGENTS.md, CLAUDE.md) may be in the system prompt, in
    # tokens; a longer one is cut at a paragraph boundary. 0 = every document whole.
    project_doc_max_tokens: int = 0
    # Refuse to run when max_cost_usd is set but the model has no known price.
    require_price: bool = False
    # Replace secret-looking values (credential-named environment values, key and token shapes) with
    # [REDACTED] in the output of commands and external tools before the model sees it. See core/secrets.py.
    mask_secrets: bool = True
    mask_extra_patterns: list[str] = Field(default_factory=list)
    # Model to switch to, once per session, when the run shows trouble ("model" or "provider:model"); empty = never.
    escalation_model: str = ""
    # Signal name -> how many occurrences trigger the switch (see core/signals.py).
    escalation_signals: dict[str, int] = Field(default_factory=lambda: {"verify_failed": 1, "repeat_refused": 1, "cas_rejected": 3, "new_diagnostics": 4})
    # Tell the model once when it has made this many edits in a row to one file that all failed, or has
    # spent this many turns in a row only reading and searching; 0 turns a check off. Counted as `no_progress`.
    no_progress_failed_edits: int = 3
    no_progress_read_turns:   int = 12
    # MCP tools whose full declarations are sent only after the model loads them with ToolSearch:
    # "auto" defers them once their declarations pass defer_tools_threshold tokens, "always", "never".
    defer_tools: Literal["auto", "always", "never"] = "auto"
    defer_tools_threshold: int = 3000
    # Share of the cost still unspent that one sub-agent (Agent call or Swarm) may use; it stops at its
    # share and its spend counts against max_cost_usd. 0 = no share, sub-agents run unbounded.
    subagent_budget_fraction: float = 0.5
    max_context_tokens: int = 180_000
    compact_threshold: float = 0.8
    compact_max_failures: int = 3  # circuit breaker max consecutive failures
    # Replace old read-type tool output with a placeholder before compaction (core/observation_mask.py):
    # the last mask_keep_last tool results stay, and nothing is cleared until the clearable
    # results add up to mask_trigger_tokens, so the request prefix changes once per batch.
    observation_masking: bool = False
    mask_keep_last: int = 6
    mask_trigger_tokens: int = 20_000
    # Name the files a Bash command changed in a git working tree at the end of its output.
    report_bash_changes: bool = False
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
    # After a file edit, ask the language server (when one is running) for
    # errors the edit introduced and append them to the tool result.
    post_edit_diagnostics: bool = True
    # Sub-agents (Agent, Swarm) running at once against one provider.
    max_parallel_agents: int = 5
    # What text typed while the agent works does: "queue" holds it for the next step, "interrupt" also
    # stops the step in progress (stream or running tools) and starts the next one with the text.
    steer_mode: Literal["queue", "interrupt"] = "queue"


class ParismConfig(BaseModel):
    enabled: bool = True
    config_path: str = ""
    format: str = "json"
    fallback_to_bash: bool = True


class HookConfig(BaseModel):
    # Project-local hooks (<cwd>/.nerdvana/hooks/*.py) execute code carried
    # by the repository, so they stay off until the user opts in and
    # approves each file's digest. Project skills (<cwd>/.agents/skills,
    # <cwd>/.nerdvana/skills) are gated the same way. See core.user_hooks.
    allow_project_hooks: bool = False


class SandboxConfig(BaseModel):
    # Confine shell commands to a write scope with the operating system (Linux
    # Landlock). "off" changes nothing, "auto" confines where the system supports it and
    # warns once where it does not, "require" refuses to run a command it cannot confine.
    mode: Literal["off", "auto", "require"] = "off"
    # False also refuses TCP connections and binds from the command (needs Linux 6.7). "allowlist"
    # lets the command reach only the local egress proxy, which passes the hosts in allowed_domains.
    network: bool | Literal["allowlist"] = True
    # Host names (exact, or *.suffix for subdomains) the egress proxy lets through when network is "allowlist".
    allowed_domains: list[str] = Field(default_factory=list)
    # Writable in addition to the project directory and the temporary directories.
    write_paths: list[str] = Field(default_factory=list)
    # The project directory and the temporary directories are writable unless these are turned off.
    # Agent definitions with a write_scope set them; see docs/agents.md.
    project_writable: bool = True
    scratch_writable: bool = True
    # Paths (relative to the project) that FileWrite, FileEdit and the symbol edit tools may change;
    # None = anywhere the permissions allow. Applies to the tools, which Landlock cannot confine.
    edit_scope: list[str] | None = None

    @field_validator("allowed_domains")
    @classmethod
    def _normalize_domains(cls, entries: list[str]) -> list[str]:
        return [normalize_pattern(entry) for entry in entries]


class SecretsConfig(BaseModel):
    # Domain (or *.suffix) -> credential the egress proxy adds to plain HTTP requests for it, so the token
    # stays out of the command's environment: "VARIABLE" sends Authorization: Bearer <value of VARIABLE>,
    # "Header-Name:VARIABLE" sends the value as it is. The domain must also be in sandbox.allowed_domains.
    proxy_credentials: dict[str, str] = Field(default_factory=dict)

    @field_validator("proxy_credentials")
    @classmethod
    def _check_credentials(cls, entries: dict[str, str]) -> dict[str, str]:
        checked = {normalize_pattern(domain): spec for domain, spec in entries.items()}
        for domain, spec in checked.items():
            ProxyCredential.parse(domain, spec)
        return checked


class CheckpointConfig(BaseModel):
    enabled: bool = True
    per_session_max: int = 50


# Keys earlier versions accepted but never acted on.
_REMOVED_KEYS = frozenset({"hooks.session_start", "hooks.before_tool", "hooks.after_tool"})

_TOP_LEVEL_KEYS = frozenset({
    "model", "permissions", "session", "parism", "hooks", "checkpoint", "skills", "agents", "sandbox", "secrets", "goal", "telemetry", "memory", "workflow", "tools",
    "model_history", "external_projects_enabled", "cwd", "verbose", "config_path",
    # Per-provider keys saved by /provider and read back by the model commands.
    "api_keys",
})

# Fields that are never softened. The sentinel marks a section whose every
# field governs permissions.
_ALL_FIELDS_STRICT = frozenset({"*"})
_MODEL_STRICT_FIELDS = frozenset({"api_key"})
_HOOKS_STRICT_FIELDS = frozenset({"allow_project_hooks"})
_SANDBOX_STRICT_FIELDS = frozenset({"mode", "network", "allowed_domains"})


def _is_project_file(path: str) -> bool:
    """True for the ``nerdvana.yml`` / ``nerdvana.yaml`` found in the working directory."""
    return os.path.abspath(path) in {os.path.abspath(os.path.join(os.getcwd(), name)) for name in ("nerdvana.yml", "nerdvana.yaml")}
_MEMORY_STRICT_FIELDS = frozenset({"review"})
_WORKFLOW_STRICT_FIELDS = frozenset({"enabled"})


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
            kind: Literal["unknown_key", "removed_key"] = (
                "removed_key" if f"{name}.{key}" in _REMOVED_KEYS else "unknown_key"
            )
            warnings.append(SettingsWarning(kind, f"{name}.{key}", type(value).__name__, ""))

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


def apply_settings_overrides(settings: NerdvanaSettings, assignments: list[str]) -> None:
    """Apply ``section.field=value`` overrides (the value read as YAML) to *settings*, validating each one.

    Raises ValueError for an unknown field or a value the field rejects. The sections that govern
    permissions and hooks cannot be overridden this way.
    """
    for assignment in assignments:
        path, sep, raw = assignment.partition("=")
        section_name, dot, field_name = path.strip().partition(".")
        if not sep or not dot or not field_name:
            raise ValueError(f"--set expects section.field=value, got '{assignment}'")
        if section_name in _NO_OVERRIDE_SECTIONS:
            raise ValueError(f"--set cannot change the '{section_name}' section")
        section = getattr(settings, section_name, None)
        if not isinstance(section, BaseModel) or field_name not in type(section).model_fields:
            raise ValueError(f"unknown setting '{path.strip()}'")
        try:
            type(section).__pydantic_validator__.validate_assignment(section, field_name, yaml.safe_load(raw))
        except Exception as exc:  # noqa: BLE001 - pydantic's error text says what is wrong
            raise ValueError(f"invalid value for '{path.strip()}': {exc}") from exc


# Sections whose fields decide what the agent may do; a command line override must not loosen them quietly.
_NO_OVERRIDE_SECTIONS = frozenset({"permissions", "hooks", "sandbox", "secrets"})

# Sections read from the config file as they are, with the fields that must never be softened.
_PLAIN_SECTIONS: tuple[tuple[str, type[BaseModel], frozenset[str]], ...] = (
    ("parism",     ParismConfig,     frozenset()),
    ("hooks",      HookConfig,       _HOOKS_STRICT_FIELDS),
    ("checkpoint", CheckpointConfig, frozenset()),
    ("skills",     SkillsConfig,     frozenset()),
    ("sandbox",    SandboxConfig,    _SANDBOX_STRICT_FIELDS),
    ("secrets",    SecretsConfig,    _ALL_FIELDS_STRICT),
    ("agents",     AgentsConfig,     frozenset()),
    ("goal",       GoalConfig,       frozenset()),
    ("telemetry",  TelemetryConfig,  frozenset()),
    ("memory",     MemoryConfig,     _MEMORY_STRICT_FIELDS),
    ("workflow",   WorkflowConfig,   _WORKFLOW_STRICT_FIELDS),
    ("tools",      ToolsConfig,      frozenset()),
)


class NerdvanaSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="NERDVANA_", env_file=".env", extra="ignore")

    model: ModelConfig = Field(default_factory=ModelConfig)
    permissions: PermissionConfig = Field(default_factory=PermissionConfig)
    session: SessionConfig = Field(default_factory=SessionConfig)
    parism: ParismConfig = Field(default_factory=ParismConfig)
    hooks: HookConfig = Field(default_factory=HookConfig)
    checkpoint: CheckpointConfig = Field(default_factory=CheckpointConfig)
    skills: SkillsConfig = Field(default_factory=SkillsConfig)
    agents: AgentsConfig = Field(default_factory=AgentsConfig)
    sandbox: SandboxConfig = Field(default_factory=SandboxConfig)
    secrets: SecretsConfig = Field(default_factory=SecretsConfig)
    goal: GoalConfig = Field(default_factory=GoalConfig)
    telemetry: TelemetryConfig = Field(default_factory=TelemetryConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    workflow: WorkflowConfig = Field(default_factory=WorkflowConfig)
    tools: ToolsConfig = Field(default_factory=ToolsConfig)
    model_history: dict[str, str] = Field(default_factory=dict)
    # External project tools hand a registered directory to a read-capable
    # subprocess, so the whole family stays off until the user opts in.
    # See tools.external_project_tools.
    external_projects_enabled: bool = False
    cwd: str = "."
    verbose: bool = False
    config_path: str = ""
    _load_warnings: list[SettingsWarning] = PrivateAttr(default_factory=list)
    _managed_policy: ManagedPolicy | None = PrivateAttr(default=None)

    @property
    def load_warnings(self) -> list[SettingsWarning]:
        """Recoverable problems recorded by the last ``load`` call."""
        return self._load_warnings

    @property
    def managed_policy(self) -> ManagedPolicy:
        """The managed policy read by the last ``load``; an empty policy for settings built another way."""
        if self._managed_policy is None:
            from nerdvana_cli.core.managed_policy import ManagedPolicy

            self._managed_policy = ManagedPolicy()
        return self._managed_policy

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
                if "telemetry" in data and _is_project_file(path):
                    # Where traces go is the user's call, not a repository's.
                    data = {key: value for key, value in data.items() if key != "telemetry"}
                    warnings.append(SettingsWarning("user_only_key", "telemetry", "dict", ""))
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
                for name, section_cls, strict in _PLAIN_SECTIONS:
                    if name in data:
                        setattr(settings, name, _build_section(section_cls, name, data[name], warnings, strict))
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

        settings._finish_defaults(user_set_context)
        settings._apply_managed_policy()
        return settings

    def _apply_managed_policy(self) -> None:
        """Read the managed settings files and force their controls onto these settings; a malformed file stops the start."""
        from nerdvana_cli.core.managed_policy import ManagedPolicyError, load_managed_policy

        try:
            self._managed_policy = load_managed_policy()
        except ManagedPolicyError as exc:
            raise SettingsLoadError(str(exc)) from exc
        self._managed_policy.apply(self)

    def _finish_defaults(self, user_set_context: bool) -> None:
        """Fill the provider, API key and context window the config file left open."""
        # Use canonical detect_provider from providers.base
        if not self.model.provider:
            from nerdvana_cli.providers.base import detect_provider
            self.model.provider = detect_provider(self.model.model).value

        # Use canonical resolve_api_key from providers.factory
        if not self.model.api_key:
            from nerdvana_cli.providers.base import ProviderName
            from nerdvana_cli.providers.factory import resolve_api_key
            try:
                prov = ProviderName(self.model.provider)
                self.model.api_key = resolve_api_key(prov)
            except ValueError:
                pass

        # Auto-apply model-specific context window (skipped when the file set it)
        if not user_set_context:
            from nerdvana_cli.providers.base import ProviderName, resolve_context_window
            try:
                prov = ProviderName(self.model.provider)
                self.session.max_context_tokens = resolve_context_window(prov, self.model.model)
            except ValueError:
                pass

    def to_api_params(self) -> dict[str, Any]:
        return {
            "model": self.model.model,
            "max_tokens": self.model.max_tokens,
            "temperature": self.model.temperature,
        }
