"""Provider abstraction layer — unified interface for all AI platforms."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable


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

    type: str  # content_delta, tool_use_start, tool_use_delta, tool_use_complete, done, error
    content: str = ""
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
PROVIDER_CAPABILITIES: dict[ProviderName, dict[str, Any]] = {
    ProviderName.ANTHROPIC: {
        "supports_tools": True,
        "supports_streaming": True,
        "supports_vision": True,
        "supports_thinking": True,
        "max_context": 200_000,
    },
    ProviderName.OPENAI: {
        "supports_tools": True,
        "supports_streaming": True,
        "supports_vision": True,
        "supports_thinking": False,
        "max_context": 1_048_576,
    },
    ProviderName.GEMINI: {
        "supports_tools": True,
        "supports_streaming": True,
        "supports_vision": True,
        "supports_thinking": True,
        "max_context": 1_048_576,
    },
    ProviderName.GROQ: {
        "supports_tools": True,
        "supports_streaming": True,
        "supports_vision": False,
        "supports_thinking": False,
        "max_context": 32_768,
    },
    ProviderName.OPENROUTER: {
        "supports_tools": True,
        "supports_streaming": True,
        "supports_vision": True,
        "supports_thinking": False,
        "max_context": 200_000,
    },
    ProviderName.XAI: {
        "supports_tools": True,
        "supports_streaming": True,
        "supports_vision": False,
        "supports_thinking": False,
        "max_context": 131_072,
    },
    ProviderName.OLLAMA: {
        "supports_tools": True,
        "supports_streaming": True,
        "supports_vision": True,
        "supports_thinking": False,
        "max_context": 131_072,
    },
    ProviderName.VLLM: {
        "supports_tools": True,
        "supports_streaming": True,
        "supports_vision": False,
        "supports_thinking": False,
        "max_context": 131_072,
    },
    ProviderName.DEEPSEEK: {
        "supports_tools": True,
        "supports_streaming": True,
        "supports_vision": False,
        "supports_thinking": True,
        "max_context": 65_536,
    },
    ProviderName.MISTRAL: {
        "supports_tools": True,
        "supports_streaming": True,
        "supports_vision": True,
        "supports_thinking": False,
        "max_context": 128_000,
    },
    ProviderName.COHERE: {
        "supports_tools": True,
        "supports_streaming": True,
        "supports_vision": False,
        "supports_thinking": False,
        "max_context": 128_000,
    },
    ProviderName.TOGETHER: {
        "supports_tools": True,
        "supports_streaming": True,
        "supports_vision": False,
        "supports_thinking": False,
        "max_context": 131_072,
    },
    ProviderName.ZAI: {
        "supports_tools": True,
        "supports_streaming": True,
        "supports_vision": False,
        "supports_thinking": False,
        "max_context": 128_000,
    },
}

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
}


def resolve_context_window(provider: ProviderName, model: str) -> int:
    """Resolve context window size for a given model.

    Uses longest-prefix matching against MODEL_CONTEXT_WINDOWS.
    Falls back to provider-level max_context from PROVIDER_CAPABILITIES.
    """
    best_match = ""
    best_value = 0
    model_lower = model.lower()
    for prefix, ctx_size in MODEL_CONTEXT_WINDOWS.items():
        if model_lower.startswith(prefix.lower()) and len(prefix) > len(best_match):
            best_match = prefix
            best_value = ctx_size
    if best_match:
        return best_value
    caps = PROVIDER_CAPABILITIES.get(provider, {})
    return caps.get("max_context", 180_000)


# Default base URLs per provider
DEFAULT_BASE_URLS: dict[ProviderName, str] = {
    ProviderName.ANTHROPIC: "https://api.anthropic.com",
    ProviderName.OPENAI: "https://api.openai.com/v1",
    ProviderName.GEMINI: "https://generativelanguage.googleapis.com",
    ProviderName.GROQ: "https://api.groq.com/openai/v1",
    ProviderName.OPENROUTER: "https://openrouter.ai/api/v1",
    ProviderName.XAI: "https://api.x.ai/v1",
    ProviderName.OLLAMA: "http://localhost:11434/v1",
    ProviderName.VLLM: "http://localhost:8000/v1",
    ProviderName.DEEPSEEK: "https://api.deepseek.com",
    ProviderName.MISTRAL: "https://api.mistral.ai/v1",
    ProviderName.COHERE: "https://api.cohere.com/v2",
    ProviderName.TOGETHER: "https://api.together.xyz/v1",
    ProviderName.ZAI: "https://api.z.ai/api/coding/paas/v4/",
}

# Default models per provider
DEFAULT_MODELS: dict[ProviderName, str] = {
    ProviderName.ANTHROPIC: "claude-sonnet-4-20250514",
    ProviderName.OPENAI: "gpt-4.1",
    ProviderName.GEMINI: "gemini-2.5-flash",
    ProviderName.GROQ: "llama-3.3-70b-versatile",
    ProviderName.OPENROUTER: "anthropic/claude-sonnet-4",
    ProviderName.XAI: "grok-3",
    ProviderName.OLLAMA: "qwen3",
    ProviderName.VLLM: "Qwen/Qwen3-32B",
    ProviderName.DEEPSEEK: "deepseek-chat",
    ProviderName.MISTRAL: "mistral-medium-latest",
    ProviderName.COHERE: "command-r-plus",
    ProviderName.TOGETHER: "meta-llama/Llama-3.3-70B-Instruct-Turbo",
    ProviderName.ZAI: "glm-4.7",
}

# Environment variable names for API keys
PROVIDER_KEY_ENVVARS: dict[ProviderName, list[str]] = {
    ProviderName.ANTHROPIC: ["ANTHROPIC_API_KEY"],
    ProviderName.OPENAI: ["OPENAI_API_KEY"],
    ProviderName.GEMINI: ["GEMINI_API_KEY", "GOOGLE_API_KEY"],
    ProviderName.GROQ: ["GROQ_API_KEY"],
    ProviderName.OPENROUTER: ["OPENROUTER_API_KEY"],
    ProviderName.XAI: ["XAI_API_KEY"],
    ProviderName.OLLAMA: ["OLLAMA_API_KEY", "OPENAI_API_KEY"],
    ProviderName.VLLM: ["VLLM_API_KEY", "OPENAI_API_KEY"],
    ProviderName.DEEPSEEK: ["DEEPSEEK_API_KEY"],
    ProviderName.MISTRAL: ["MISTRAL_API_KEY"],
    ProviderName.COHERE: ["CO_API_KEY"],
    ProviderName.TOGETHER: ["TOGETHER_API_KEY"],
    ProviderName.ZAI: ["ZHIPUAI_API_KEY"],
}


def detect_provider(model: str) -> ProviderName:
    """Detect provider from model name."""
    m = model.lower()

    # Anthropic
    if m.startswith("claude"):
        return ProviderName.ANTHROPIC

    # OpenAI
    if m.startswith(("gpt-", "o1", "o3", "o4")):
        return ProviderName.OPENAI

    # Gemini
    if m.startswith("gemini"):
        return ProviderName.GEMINI

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

    # Default to Anthropic
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
