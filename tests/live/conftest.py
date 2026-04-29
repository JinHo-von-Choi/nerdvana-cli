"""Live smoke pytest fixtures and provider key gating."""

from __future__ import annotations

import os
from collections.abc import Iterable

import pytest

MAX_LIVE_TOKENS: int = 50
LIVE_TIMEOUT:    int = 30

# Maps provider id → tuple of env var names, at least one must be set.
_PROVIDER_ENV: dict[str, tuple[str, ...]] = {
    "anthropic":  ("ANTHROPIC_API_KEY",),
    "openai":     ("OPENAI_API_KEY",),
    "gemini":     ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
    "groq":       ("GROQ_API_KEY",),
    "openrouter": ("OPENROUTER_API_KEY",),
    "xai":        ("XAI_API_KEY",),
    "ollama":     ("OLLAMA_API_KEY",),
    "vllm":       ("VLLM_API_KEY", "OPENAI_API_KEY"),
    "deepseek":   ("DEEPSEEK_API_KEY",),
    "mistral":    ("MISTRAL_API_KEY",),
    "cohere":     ("CO_API_KEY",),
    "together":   ("TOGETHER_API_KEY",),
    "zai":        ("ZHIPUAI_API_KEY",),
    "featherless":  ("FEATHERLESS_API_KEY",),
    "xiaomi_mimo":  ("MIMO_API_KEY", "XIAOMI_API_KEY"),
    "moonshot":     ("MOONSHOT_API_KEY", "KIMI_API_KEY"),
    "dashscope":    ("DASHSCOPE_API_KEY", "ALIBABA_API_KEY"),
    "minimax":      ("MINIMAX_API_KEY",),
    "perplexity":   ("PERPLEXITY_API_KEY", "PPLX_API_KEY"),
    "fireworks":    ("FIREWORKS_API_KEY",),
    "cerebras":     ("CEREBRAS_API_KEY",),
}


def _provider_keys_present(env_vars: Iterable[str]) -> bool:
    """Return True when at least one env var in *env_vars* is non-empty."""
    return any(os.environ.get(v) for v in env_vars)


def pytest_collection_modifyitems(
    config: pytest.Config,
    items: list[pytest.Item],
) -> None:
    """Skip live smoke tests when the matching provider env var is absent."""
    for item in items:
        if "live" not in item.keywords:
            continue
        for provider, env_vars in _PROVIDER_ENV.items():
            if f"smoke_{provider}" in item.name or f"/{provider}_" in item.nodeid:
                if not _provider_keys_present(env_vars):
                    item.add_marker(
                        pytest.mark.skip(
                            reason=f"{provider}: no API key found in {env_vars}"
                        )
                    )
                break
