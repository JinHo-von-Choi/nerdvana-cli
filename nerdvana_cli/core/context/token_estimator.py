"""Token estimation: one strategy interface, a local approximation and optional provider tokenizers.

Author: 최진호
Date:   2026-10-04

``TokenEstimator`` is the interface. ``CharEstimator`` is the default approximation every count
falls back to: about four ASCII characters per token, one token per other character, and one and a
half per Hangul character. ``TiktokenEstimator`` counts exactly for OpenAI-compatible models when
``tiktoken`` is installed. ``estimator_for`` picks the estimator a session counts with and never
makes a network call; ``AnthropicExactEstimator`` asks the Anthropic API and is for explicit use only.

The approximation is measured against tiktoken on English, Korean and mixed text (see
tests/core/test_token_estimator.py). On Korean prose ``cl100k_base`` spends 1.3 to 1.5 tokens per
Hangul syllable and ``o200k_base`` about 0.9, so the Hangul weight keeps the estimate from running
under either; on English it stays within 25 percent above both.
"""

from __future__ import annotations

import logging
import math
import re
from abc import ABC, abstractmethod
from functools import cache

logger = logging.getLogger(__name__)

# Hangul syllables, jamo and compatibility jamo.
_HANGUL = re.compile("[\u1100-\u11ff\u3130-\u318f\uac00-\ud7a3]")


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
    """The default approximation, always available: no tokenizer, O(n).

    ASCII text averages ``avg_chars_per_token`` characters per token (4 by default). Every other
    character counts as one token, except Hangul, which counts as one and a half.
    """

    def __init__(self, avg_chars_per_token: int = 4) -> None:
        self._avg = max(1, avg_chars_per_token)

    def estimate(self, text: str) -> int:
        ascii_chars  = len(text.encode("ascii", "ignore"))
        hangul_chars = len(_HANGUL.findall(text))
        other_chars  = len(text) - ascii_chars - hangul_chars
        return math.ceil(ascii_chars / self._avg) + other_chars + math.ceil(hangul_chars * 3 / 2)


# ---------------------------------------------------------------------------
# OpenAI / tiktoken estimator
# ---------------------------------------------------------------------------

class TiktokenEstimator(TokenEstimator):
    """Tiktoken-based exact estimator for OpenAI-compatible models.

    Falls back to CharEstimator when `tiktoken` is not installed or its encoding cannot be loaded
    (tiktoken fetches an encoding over the network the first time it is used).
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
        except Exception as exc:  # noqa: BLE001
            logger.debug("tiktoken encoding for %s unavailable (%s); TiktokenEstimator using CharEstimator fallback", model, exc)

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
        model:   str        = "claude-sonnet-5-5",
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
            return AnthropicExactEstimator(api_key=api_key, model=model or "claude-sonnet-5-5")

        if p in cls._TIKTOKEN_PROVIDERS:
            return TiktokenEstimator(model=model or "gpt-4o")

        return CharEstimator()


# ---------------------------------------------------------------------------
# Module-level convenience
# ---------------------------------------------------------------------------

_DEFAULT = CharEstimator()


@cache
def estimator_for(provider: str | None, model: str = "") -> TokenEstimator:
    """The estimator a session on *provider* counts with: a local tokenizer when one applies, else the default.

    Never a network call, so it is safe for the per-turn context accounting.
    """
    if provider and provider.lower().strip() in TokenEstimatorRegistry._TIKTOKEN_PROVIDERS:
        return TiktokenEstimator(model=model or "gpt-4o")
    return _DEFAULT


def approx_tokens(text: str) -> int:
    """Token estimate of *text* with the default approximation."""
    return _DEFAULT.estimate(text)


def estimate_tokens(text: str, provider: str | None = None) -> int:
    """Token estimate of *text* with the estimator ``TokenEstimatorRegistry`` picks for *provider*."""
    return TokenEstimatorRegistry.get_for(provider).estimate(text)
