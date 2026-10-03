"""Provider factory — creates providers based on configuration."""

from __future__ import annotations

import logging
import os
from collections.abc import Callable

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
from nerdvana_cli.providers.gemini_interactions import GeminiInteractionsProvider, uses_interactions
from nerdvana_cli.providers.gemini_provider import GeminiProvider
from nerdvana_cli.providers.openai_provider import OpenAIProvider
from nerdvana_cli.providers.openai_responses import OpenAIResponsesProvider, uses_responses

logger = logging.getLogger(__name__)

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
    ProviderName.MINIMAX: OpenAIProvider,
    ProviderName.PERPLEXITY: OpenAIProvider,
    ProviderName.FIREWORKS: OpenAIProvider,
    ProviderName.CEREBRAS: OpenAIProvider,
}


def provider_class_for(
    provider: ProviderName,
) -> type[AnthropicProvider] | type[OpenAIProvider] | type[GeminiProvider] | None:
    """Return the provider class registered for ``provider``, or None."""
    return _PROVIDER_CLASSES.get(provider)


def _select_class(config: ProviderConfig) -> type[AnthropicProvider] | type[OpenAIProvider] | type[GeminiProvider]:
    """The adapter class for *config*: the registered one, or the OpenAI Responses or Gemini Interactions variant
    when the config selects that API."""
    provider_cls = _PROVIDER_CLASSES.get(config.provider)
    if provider_cls is None:
        logger.warning(
            "Provider %s is not registered in _PROVIDER_CLASSES; using OpenAIProvider", config.provider.value
        )
        provider_cls = OpenAIProvider
    if provider_cls is OpenAIProvider and uses_responses(config):
        return OpenAIResponsesProvider
    if provider_cls is GeminiProvider and uses_interactions(config):
        return GeminiInteractionsProvider
    return provider_cls


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
    prompt_caching: bool = True,
    extended_thinking: bool = False,
    thinking_budget: int = 8192,
    show_thinking: bool = True,
    reasoning_effort: str = "",
    openai_api: str = "auto",
    gemini_api: str = "generate_content",
    anthropic_tool_search: str = "off",
    anthropic_compaction: str = "off",
    count_tokens: Callable[[str], int] | None = None,
) -> AnthropicProvider | OpenAIProvider | GeminiProvider:
    """Create a provider instance from configuration.

    Without a provider it is detected from the model name; without a model the provider's default is used.
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
        prompt_caching=prompt_caching,
        extended_thinking=extended_thinking,
        thinking_budget=thinking_budget,
        show_thinking=show_thinking,
        reasoning_effort=reasoning_effort,
        openai_api=openai_api,
        gemini_api=gemini_api,
        anthropic_tool_search=anthropic_tool_search,
        anthropic_compaction=anthropic_compaction,
        count_tokens=count_tokens,
    )

    return _select_class(config)(config)


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
