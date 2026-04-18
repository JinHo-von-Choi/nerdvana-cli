"""Token estimation abstractions for NerdVana CLI.

Provider-aware token counting. Falls back gracefully when optional
dependencies (tiktoken, anthropic) are not installed.

Hierarchy:
    TokenEstimator (ABC)
    ├── TiktokenEstimator   — OpenAI tokenizer (requires `tiktoken`)
    ├── AnthropicExactEstimator — Anthropic count_tokens API (requires `anthropic`)
    └── CharEstimator       — Char-based fallback (always available)

TokenEstimatorRegistry.get_for(provider) selects the right estimator.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Base class
# ---------------------------------------------------------------------------

class TokenEstimator(ABC):
    """Abstract token estimator."""

    @abstractmethod
    def estimate(self, text: str) -> int:
        """Return estimated token count for *text*."""
        ...


# ---------------------------------------------------------------------------
# Char-based fallback
# ---------------------------------------------------------------------------

class CharEstimator(TokenEstimator):
    """Simple character-division estimator.

    Default: 4 chars ≈ 1 token (GPT-3/4 rough rule).
    Always available — no extra dependencies.
    """

    def __init__(self, avg_chars_per_token: int = 4) -> None:
        self._avg = max(1, avg_chars_per_token)

    def estimate(self, text: str) -> int:
        import math
        return math.ceil(len(text) / self._avg)


# ---------------------------------------------------------------------------
# OpenAI / tiktoken estimator
# ---------------------------------------------------------------------------

class TiktokenEstimator(TokenEstimator):
    """Tiktoken-based exact estimator for OpenAI-compatible models.

    Falls back to CharEstimator silently when `tiktoken` is not installed.
    """

    def __init__(self, model: str = "gpt-4o") -> None:
        self._model    = model
        self._enc      = None
        self._fallback = CharEstimator()

        try:
            import tiktoken
            try:
                self._enc = tiktoken.encoding_for_model(model)
            except KeyError:
                # Model not in tiktoken's registry — use cl100k_base
                self._enc = tiktoken.get_encoding("cl100k_base")
        except ImportError:
            logger.debug("tiktoken not installed; TiktokenEstimator using CharEstimator fallback")

    def estimate(self, text: str) -> int:
        if self._enc is not None:
            return len(self._enc.encode(text))
        return self._fallback.estimate(text)


# ---------------------------------------------------------------------------
# Anthropic exact estimator
# ---------------------------------------------------------------------------

class AnthropicExactEstimator(TokenEstimator):
    """Anthropic count_tokens API for accurate Claude token counts.

    Requires `anthropic` package and a valid API key.
    Falls back to CharEstimator when the SDK is unavailable or the API call
    fails.
    """

    def __init__(
        self,
        api_key: str | None = None,
        model:   str        = "claude-sonnet-4-20250514",
    ) -> None:
        self._model    = model
        self._api_key  = api_key
        self._client   = None
        self._fallback = CharEstimator()

        try:
            import os

            import anthropic
            key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
            if key:
                self._client = anthropic.Anthropic(api_key=key)
        except ImportError:
            logger.debug("anthropic SDK not installed; AnthropicExactEstimator using CharEstimator fallback")

    def estimate(self, text: str) -> int:
        if self._client is None:
            return self._fallback.estimate(text)
        try:
            response = self._client.messages.count_tokens(
                model    = self._model,
                messages = [{"role": "user", "content": text}],
            )
            return response.input_tokens
        except Exception as exc:  # noqa: BLE001
            logger.debug("Anthropic count_tokens failed (%s); using fallback", exc)
            return self._fallback.estimate(text)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

class TokenEstimatorRegistry:
    """Factory that selects the right estimator for a given provider string.

    Selection rules:
    - "anthropic" → AnthropicExactEstimator (if API key available, else Char)
    - "openai" | "groq" | "mistral" | "deepseek" | "fireworks" → TiktokenEstimator
    - None or anything else → CharEstimator
    """

    _TIKTOKEN_PROVIDERS: frozenset[str] = frozenset({
        "openai", "groq", "mistral", "deepseek", "fireworks",
    })

    @classmethod
    def get_for(
        cls,
        provider:   str | None = None,
        model:      str | None = None,
        api_key:    str | None = None,
    ) -> TokenEstimator:
        """Return the best available estimator for *provider*."""
        if provider is None:
            return CharEstimator()

        p = provider.lower().strip()

        if p == "anthropic":
            return AnthropicExactEstimator(api_key=api_key, model=model or "claude-sonnet-4-20250514")

        if p in cls._TIKTOKEN_PROVIDERS:
            return TiktokenEstimator(model=model or "gpt-4o")

        return CharEstimator()


# ---------------------------------------------------------------------------
# Module-level convenience (drop-in replacement for agent_loop.estimate_tokens)
# ---------------------------------------------------------------------------

def estimate_tokens(text: str, provider: str | None = None) -> int:
    """Estimate token count. Compatible with the original agent_loop signature.

    provider=None → CharEstimator (identical behaviour to the original len/4).
    """
    return TokenEstimatorRegistry.get_for(provider).estimate(text)
