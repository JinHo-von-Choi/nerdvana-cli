"""Provider abstraction layer — unified interface for all AI platforms."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

logger = logging.getLogger(__name__)


class ProviderName(StrEnum):
    """Supported AI providers."""

    ANTHROPIC = "anthropic"
    OPENAI = "openai"
    GEMINI = "gemini"
    GROQ = "groq"
    OPENROUTER = "openrouter"
    XAI = "xai"
    OLLAMA = "ollama"
    VLLM = "vllm"
    DEEPSEEK = "deepseek"
    MISTRAL = "mistral"
    COHERE = "cohere"
    TOGETHER = "together"
    ZAI = "zai"
    FEATHERLESS = "featherless"
    XIAOMI_MIMO = "xiaomi_mimo"
    MOONSHOT = "moonshot"
    DASHSCOPE = "dashscope"
    MINIMAX = "minimax"
    PERPLEXITY = "perplexity"
    FIREWORKS = "fireworks"
    CEREBRAS = "cerebras"


@dataclass
class ProviderResponse:
    """Normalized response from any provider."""

    content: str = ""
    tool_use_id: str = ""
    tool_name: str = ""
    tool_input: dict[str, Any] | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    stop_reason: str = ""  # end_turn, tool_use, max_tokens
    thinking: str = ""
    is_error: bool = False


@dataclass
class ProviderEvent:
    """Streaming event from provider."""

    type: str  # content_delta, thinking_delta, tool_use_start, tool_use_delta, tool_use_complete, done, error
    content: str = ""
    thinking: str = ""
    tool_use_id: str = ""
    tool_name: str = ""
    tool_input_delta: str = ""
    tool_input_complete: dict[str, Any] | None = None
    usage: dict[str, int] | None = None
    stop_reason: str = ""
    error: str = ""


@dataclass
class ProviderConfig:
    """Provider configuration."""

    provider: ProviderName = ProviderName.ANTHROPIC
    api_key: str = ""
    base_url: str = ""
    model: str = ""
    max_tokens: int = 8192
    temperature: float = 1.0
    extra: dict[str, Any] = field(default_factory=dict)

    def __repr__(self) -> str:
        masked_key = ""
        if self.api_key:
            masked_key = self.api_key[:4] + "****" + self.api_key[-4:] if len(self.api_key) > 8 else "****"
        return (
            f"ProviderConfig(provider={self.provider!r}, model={self.model!r}, "
            f"api_key={masked_key!r}, base_url={self.base_url!r})"
        )


@dataclass
class ModelInfo:
    """Model metadata from provider API."""

    id: str
    name: str = ""
    provider: str = ""
    context_window: int = 0
    created: str = ""

    @property
    def display_name(self) -> str:
        return self.name if self.name else self.id


@runtime_checkable
class BaseProvider(Protocol):
    """Protocol that all provider implementations must satisfy."""

    async def stream(
        self,
        system_prompt: str,
        messages: list[dict[str, Any]],
        tools: list[Any],
    ) -> AsyncIterator[ProviderEvent]: ...

    async def send(
        self,
        system_prompt: str,
        messages: list[dict[str, Any]],
        tools: list[Any],
    ) -> dict[str, Any]: ...

    async def list_models(self) -> list[ModelInfo]:
        """Return available models for this provider.

        Default returns an empty list. Concrete providers should override
        this when the upstream API exposes a `/models` endpoint. An empty
        return value MUST NOT be interpreted as a key validation failure —
        it only means model enumeration is unavailable for this provider.
        """
        return []


# Provider capability matrix
def _load_variants() -> dict[str, dict[str, Any]]:
    """Load provider variant metadata from ``variants.yml`` (single source of truth)."""
    from pathlib import Path

    import yaml  # type: ignore[import-untyped]

    with Path(__file__).with_name("variants.yml").open(encoding="utf-8") as fh:
        data: dict[str, dict[str, Any]] = yaml.safe_load(fh) or {}
    return data


_VARIANTS: dict[str, dict[str, Any]] = _load_variants()


def _by_provider(field: str) -> dict[ProviderName, Any]:
    """Project a single ``variants.yml`` field across all entries, keyed by ProviderName."""
    return {ProviderName(name): entry[field] for name, entry in _VARIANTS.items()}


PROVIDER_CAPABILITIES: dict[ProviderName, dict[str, Any]] = _by_provider("capabilities")

# Model-specific context window sizes (prefix-matched)
MODEL_CONTEXT_WINDOWS: dict[str, int] = {
    "claude-opus-4": 200_000,
    "claude-sonnet-4": 200_000,
    "claude-haiku": 200_000,
    "gpt-4.1": 1_048_576,
    "gpt-4o-mini": 128_000,
    "gpt-4o": 128_000,
    "gpt-4-turbo": 128_000,
    "o3-mini": 200_000,
    "o3": 200_000,
    "o4-mini": 200_000,
    "gemini-2.5-pro": 1_048_576,
    "gemini-2.5-flash": 1_048_576,
    "gemini-2.0": 1_048_576,
    "llama-3.3": 32_768,
    "llama-3.1": 131_072,
    "mixtral": 32_768,
    "gemma2": 8_192,
    "deepseek-chat": 65_536,
    "deepseek-reasoner": 65_536,
    "mistral-large": 128_000,
    "mistral-medium": 128_000,
    "codestral": 256_000,
    "command-r-plus": 128_000,
    "command-r": 128_000,
    "kimi-k2": 131_072,
    "kimi-latest": 131_072,
    "moonshot-v1-128k": 131_072,
    "moonshot-v1-32k": 32_768,
    "moonshot-v1-8k": 8_192,
    "qwen3-coder-plus": 1_000_000,
    "qwen3-coder": 256_000,
    "qwen-max": 32_768,
    "qwen-plus": 131_072,
    "qwen-turbo": 1_000_000,
    "qwen-vl-max": 32_768,
    "qwen-vl": 32_768,
    "qwq-": 131_072,
    "MiniMax-M2": 1_000_000,
    "minimax-m2": 1_000_000,
    "abab6": 245_000,
    "sonar-pro": 200_000,
    "sonar-reasoning-pro": 127_000,
    "sonar-": 127_000,
    "accounts/fireworks/models/llama-v3p3-70b-instruct": 131_072,
    "accounts/fireworks/models/qwen2p5-72b": 131_072,
    "accounts/fireworks/models/": 131_072,
    "llama-3.3-70b": 131_072,
    "llama-4-scout": 131_072,
    "qwen-3-32b": 65_536,
    "qwen-3-235b": 131_072,
}


_CEREBRAS_MODELS: frozenset[str] = frozenset({
    "llama-3.3-70b",
    "llama-3.1-8b",
    "llama-4-scout-17b-16e-instruct",
    "qwen-3-32b",
    "qwen-3-235b-a22b-instruct-2507",
})


# Provider-specific context window overrides that take precedence over the
# generic prefix table.  Keyed by (provider, model_prefix_lower).
_PROVIDER_MODEL_CONTEXT_OVERRIDES: dict[tuple[ProviderName, str], int] = {
    (ProviderName.GROQ, "llama-3.3-70b"): 32_768,
}


def resolve_context_window(provider: ProviderName, model: str) -> int:
    """Resolve context window size for a given model.

    Provider-specific overrides are checked first, then longest-prefix
    matching against MODEL_CONTEXT_WINDOWS.  Falls back to provider-level
    max_context from PROVIDER_CAPABILITIES.
    """
    model_lower = model.lower()
    for (prov, prefix), ctx_size in _PROVIDER_MODEL_CONTEXT_OVERRIDES.items():
        if prov == provider and model_lower.startswith(prefix.lower()):
            return ctx_size
    best_match = ""
    best_value = 0
    for prefix, ctx_size in MODEL_CONTEXT_WINDOWS.items():
        if model_lower.startswith(prefix.lower()) and len(prefix) > len(best_match):
            best_match = prefix
            best_value = ctx_size
    if best_match:
        return best_value
    caps = PROVIDER_CAPABILITIES.get(provider, {})
    return int(caps.get("max_context", 180_000))


# Default base URLs per provider
DEFAULT_BASE_URLS: dict[ProviderName, str] = _by_provider("base_url")

# Default models per provider
DEFAULT_MODELS: dict[ProviderName, str] = _by_provider("default_model")

# Environment variable names for API keys
PROVIDER_KEY_ENVVARS: dict[ProviderName, list[str]] = _by_provider("env_vars")


def detect_provider(model: str) -> ProviderName:
    """Detect provider from model name."""
    m = model.lower()

    # Anthropic
    if m.startswith("claude"):
        return ProviderName.ANTHROPIC

    # Fireworks AI — model IDs always carry this exact prefix
    if m.startswith("accounts/fireworks/models/"):
        return ProviderName.FIREWORKS

    # MiniMax
    if m.startswith(("minimax-", "abab")) or model.startswith("MiniMax-"):
        return ProviderName.MINIMAX

    # Perplexity
    if m.startswith(("sonar-", "pplx-")):
        return ProviderName.PERPLEXITY

    # OpenAI
    if m.startswith(("gpt-", "o1", "o3", "o4")):
        return ProviderName.OPENAI

    # Gemini
    if m.startswith("gemini"):
        return ProviderName.GEMINI

    # Moonshot AI (Kimi)
    if m.startswith(("kimi", "moonshot-")):
        return ProviderName.MOONSHOT

    # Alibaba DashScope (Qwen Cloud)
    if m.startswith((
        "qwen-max", "qwen-plus", "qwen-turbo",
        "qwen-vl", "qwen3-coder", "qwen3-72b-instruct", "qwq-",
    )):
        return ProviderName.DASHSCOPE

    # Cerebras — exact catalog whitelist (avoids Groq llama-* collision)
    if m in _CEREBRAS_MODELS:
        return ProviderName.CEREBRAS

    # Groq
    if m.startswith(("llama-", "mixtral-", "gemma-", "qwen-")):
        return ProviderName.GROQ

    # DeepSeek
    if m.startswith("deepseek"):
        return ProviderName.DEEPSEEK

    # Mistral
    if m.startswith(("mistral", "codestral", "pixtral")):
        return ProviderName.MISTRAL

    # Cohere
    if m.startswith(("command", "c4ai")):
        return ProviderName.COHERE

    # xAI
    if m.startswith("grok"):
        return ProviderName.XAI

    # Z.AI (GLM)
    if m.startswith("glm"):
        return ProviderName.ZAI

    # Featherless — models typically prefixed with provider name
    if m.startswith("featherless-") or "/featherless" in m:
        return ProviderName.FEATHERLESS

    # Xiaomi MiMo — model names start with 'mimo-'
    if m.startswith("mimo-"):
        return ProviderName.XIAOMI_MIMO

    # Ollama — distinctive tag-separated naming (qwen3:latest, gemma4:31b-cloud, ...)
    if ":" in m or m.endswith("-cloud"):
        return ProviderName.OLLAMA

    # Default to Anthropic
    logger.error("Unknown model prefix '%s' — defaulting to Anthropic", model)
    return ProviderName.ANTHROPIC


def get_provider_config(
    provider: ProviderName | str,
    api_key: str = "",
    base_url: str = "",
    model: str = "",
    max_tokens: int = 8192,
    temperature: float = 1.0,
) -> ProviderConfig:
    """Build ProviderConfig from provider name + overrides."""
    if isinstance(provider, str):
        provider = ProviderName(provider)

    if not api_key:
        import os

        for var_name in PROVIDER_KEY_ENVVARS.get(provider, []):
            api_key = os.environ.get(var_name, "")
            if api_key:
                break

    if not base_url:
        base_url = DEFAULT_BASE_URLS.get(provider, "")

    if not model:
        model = DEFAULT_MODELS.get(provider, "gpt-4.1")

    return ProviderConfig(
        provider=provider,
        api_key=api_key,
        base_url=base_url,
        model=model,
        max_tokens=max_tokens,
        temperature=temperature,
    )
