"""Tests for nerdvana_cli.core.token_estimator."""
from __future__ import annotations

import math

import pytest


# ---------------------------------------------------------------------------
# CharEstimator
# ---------------------------------------------------------------------------

class TestCharEstimator:
    def test_basic_estimate(self) -> None:
        from nerdvana_cli.core.token_estimator import CharEstimator
        est = CharEstimator()
        # 8 chars / 4 = 2 tokens
        assert est.estimate("hello!!") == math.ceil(7 / 4)

    def test_empty_string(self) -> None:
        from nerdvana_cli.core.token_estimator import CharEstimator
        est = CharEstimator()
        assert est.estimate("") == 0

    def test_custom_avg(self) -> None:
        from nerdvana_cli.core.token_estimator import CharEstimator
        est = CharEstimator(avg_chars_per_token=2)
        assert est.estimate("abcd") == 2

    def test_ceil_rounding(self) -> None:
        from nerdvana_cli.core.token_estimator import CharEstimator
        est = CharEstimator(avg_chars_per_token=4)
        # 5 chars / 4 = 1.25 → ceil = 2
        assert est.estimate("hello") == 2


# ---------------------------------------------------------------------------
# TiktokenEstimator
# ---------------------------------------------------------------------------

class TestTiktokenEstimator:
    def test_import_and_estimate(self) -> None:
        """Should work whether or not tiktoken is installed."""
        from nerdvana_cli.core.token_estimator import TiktokenEstimator
        est = TiktokenEstimator(model="gpt-4o")
        result = est.estimate("Hello, world!")
        assert isinstance(result, int)
        assert result > 0

    def test_empty_string(self) -> None:
        from nerdvana_cli.core.token_estimator import TiktokenEstimator
        est = TiktokenEstimator()
        assert est.estimate("") == 0

    def test_unknown_model_fallback(self) -> None:
        """Unknown model should not raise — uses cl100k_base or CharEstimator."""
        from nerdvana_cli.core.token_estimator import TiktokenEstimator
        est = TiktokenEstimator(model="nonexistent-model-xyz")
        result = est.estimate("test input")
        assert result > 0


# ---------------------------------------------------------------------------
# AnthropicExactEstimator
# ---------------------------------------------------------------------------

class TestAnthropicExactEstimator:
    def test_no_key_uses_fallback(self) -> None:
        """Without API key, should fall back to CharEstimator gracefully."""
        from nerdvana_cli.core.token_estimator import AnthropicExactEstimator
        est = AnthropicExactEstimator(api_key="")
        result = est.estimate("Some text here")
        assert isinstance(result, int)
        assert result > 0

    def test_empty_string_no_key(self) -> None:
        from nerdvana_cli.core.token_estimator import AnthropicExactEstimator
        est = AnthropicExactEstimator(api_key="")
        assert est.estimate("") == 0


# ---------------------------------------------------------------------------
# TokenEstimatorRegistry
# ---------------------------------------------------------------------------

class TestTokenEstimatorRegistry:
    def test_none_provider_returns_char(self) -> None:
        from nerdvana_cli.core.token_estimator import CharEstimator, TokenEstimatorRegistry
        est = TokenEstimatorRegistry.get_for(None)
        assert isinstance(est, CharEstimator)

    def test_anthropic_provider(self) -> None:
        from nerdvana_cli.core.token_estimator import AnthropicExactEstimator, TokenEstimatorRegistry
        est = TokenEstimatorRegistry.get_for("anthropic", api_key="")
        assert isinstance(est, AnthropicExactEstimator)

    def test_openai_provider(self) -> None:
        from nerdvana_cli.core.token_estimator import TiktokenEstimator, TokenEstimatorRegistry
        est = TokenEstimatorRegistry.get_for("openai")
        assert isinstance(est, TiktokenEstimator)

    def test_groq_provider(self) -> None:
        from nerdvana_cli.core.token_estimator import TiktokenEstimator, TokenEstimatorRegistry
        est = TokenEstimatorRegistry.get_for("groq")
        assert isinstance(est, TiktokenEstimator)

    def test_ollama_provider_returns_char(self) -> None:
        from nerdvana_cli.core.token_estimator import CharEstimator, TokenEstimatorRegistry
        est = TokenEstimatorRegistry.get_for("ollama")
        assert isinstance(est, CharEstimator)

    def test_unknown_provider_returns_char(self) -> None:
        from nerdvana_cli.core.token_estimator import CharEstimator, TokenEstimatorRegistry
        est = TokenEstimatorRegistry.get_for("unknown-provider")
        assert isinstance(est, CharEstimator)

    def test_case_insensitive(self) -> None:
        from nerdvana_cli.core.token_estimator import TiktokenEstimator, TokenEstimatorRegistry
        est = TokenEstimatorRegistry.get_for("OpenAI")
        assert isinstance(est, TiktokenEstimator)

    def test_mistral_provider(self) -> None:
        from nerdvana_cli.core.token_estimator import TiktokenEstimator, TokenEstimatorRegistry
        est = TokenEstimatorRegistry.get_for("mistral")
        assert isinstance(est, TiktokenEstimator)


# ---------------------------------------------------------------------------
# estimate_tokens module-level convenience
# ---------------------------------------------------------------------------

class TestEstimateTokensFunction:
    def test_no_provider(self) -> None:
        from nerdvana_cli.core.token_estimator import estimate_tokens
        result = estimate_tokens("hello world")
        assert result > 0

    def test_with_provider(self) -> None:
        from nerdvana_cli.core.token_estimator import estimate_tokens
        result = estimate_tokens("hello world", provider="openai")
        assert result > 0
