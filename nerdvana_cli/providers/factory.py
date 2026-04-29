"""Provider factory — creates providers based on configuration."""

from __future__ import annotations

import os

from rich.console import Console
from rich.table import Table

from nerdvana_cli.providers.anthropic_provider import AnthropicProvider
from nerdvana_cli.providers.base import (
    DEFAULT_BASE_URLS,
    DEFAULT_MODELS,
    PROVIDER_CAPABILITIES,
    PROVIDER_KEY_ENVVARS,
    ProviderConfig,
    ProviderName,
    detect_provider,
)
from nerdvana_cli.providers.gemini_provider import GeminiProvider
from nerdvana_cli.providers.openai_provider import OpenAIProvider

console = Console()

_PROVIDER_CLASSES: dict[ProviderName, type[AnthropicProvider] | type[OpenAIProvider] | type[GeminiProvider]] = {
    ProviderName.ANTHROPIC: AnthropicProvider,
    ProviderName.OPENAI: OpenAIProvider,
    ProviderName.GEMINI: GeminiProvider,
    ProviderName.GROQ: OpenAIProvider,
    ProviderName.OPENROUTER: OpenAIProvider,
    ProviderName.XAI: OpenAIProvider,
    ProviderName.OLLAMA: OpenAIProvider,
    ProviderName.VLLM: OpenAIProvider,
    ProviderName.DEEPSEEK: OpenAIProvider,
    ProviderName.MISTRAL: OpenAIProvider,
    ProviderName.COHERE: OpenAIProvider,
    ProviderName.TOGETHER: OpenAIProvider,
    ProviderName.ZAI: OpenAIProvider,
    ProviderName.FEATHERLESS: OpenAIProvider,
    ProviderName.XIAOMI_MIMO: OpenAIProvider,
    ProviderName.MOONSHOT: OpenAIProvider,
    ProviderName.DASHSCOPE: OpenAIProvider,
}


def resolve_api_key(provider: ProviderName) -> str:
    """Resolve API key from environment variables."""
    env_vars = PROVIDER_KEY_ENVVARS.get(provider, [])
    for var in env_vars:
        val = os.environ.get(var, "")
        if val:
            return val
    return ""


def create_provider(
    provider: str | ProviderName | None = None,
    model: str = "",
    api_key: str = "",
    base_url: str = "",
    max_tokens: int = 8192,
    temperature: float = 1.0,
) -> AnthropicProvider | OpenAIProvider | GeminiProvider:
    """Create a provider instance from configuration.

    If provider is not specified, auto-detect from model name.
    If model is not specified, use provider's default model.
    """
    # Resolve provider
    if provider is None:
        provider = detect_provider(model) if model else ProviderName.ANTHROPIC
    elif isinstance(provider, str):
        provider = ProviderName(provider)

    # Resolve model
    if not model:
        model = DEFAULT_MODELS.get(provider, "gpt-4.1")

    # Resolve API key
    if not api_key:
        api_key = resolve_api_key(provider)

    # Resolve base URL
    if not base_url:
        base_url = DEFAULT_BASE_URLS.get(provider, "")

    config = ProviderConfig(
        provider=provider,
        api_key=api_key,
        base_url=base_url,
        model=model,
        max_tokens=max_tokens,
        temperature=temperature,
    )

    provider_cls = _PROVIDER_CLASSES.get(provider, OpenAIProvider)
    return provider_cls(config)


def print_providers_table() -> None:
    """Print a rich table of all supported providers."""
    table = Table(title="Supported AI Providers")
    table.add_column("Provider", style="cyan", no_wrap=True)
    table.add_column("Default Model", style="green")
    table.add_column("Base URL", style="dim")
    table.add_column("Env Var", style="yellow")
    table.add_column("Tools", justify="center")
    table.add_column("Streaming", justify="center")

    for name in ProviderName:
        caps = PROVIDER_CAPABILITIES.get(name, {})
        env_vars = PROVIDER_KEY_ENVVARS.get(name, [])
        table.add_row(
            name.value,
            DEFAULT_MODELS.get(name, ""),
            DEFAULT_BASE_URLS.get(name, ""),
            env_vars[0] if env_vars else "(none)",
            "Yes" if caps.get("supports_tools") else "No",
            "Yes" if caps.get("supports_streaming") else "No",
        )

    console.print(table)
