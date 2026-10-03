"""Tests for nerdvana_cli.core.context.token_estimator."""
from __future__ import annotations

import math

import pytest

from nerdvana_cli.core.context.compact import compact_messages
from nerdvana_cli.core.context.context_budget import ContextBudget
from nerdvana_cli.core.context.token_estimator import (
    CharEstimator,
    TiktokenEstimator,
    approx_tokens,
    estimator_for,
)
from nerdvana_cli.types import Message, Role

# ---------------------------------------------------------------------------
# CharEstimator
# ---------------------------------------------------------------------------

class TestCharEstimator:
    def test_basic_estimate(self) -> None:
        from nerdvana_cli.core.context.token_estimator import CharEstimator
        est = CharEstimator()
        # 8 chars / 4 = 2 tokens
        assert est.estimate("hello!!") == math.ceil(7 / 4)

    def test_empty_string(self) -> None:
        from nerdvana_cli.core.context.token_estimator import CharEstimator
        est = CharEstimator()
        assert est.estimate("") == 0

    def test_custom_avg(self) -> None:
        from nerdvana_cli.core.context.token_estimator import CharEstimator
        est = CharEstimator(avg_chars_per_token=2)
        assert est.estimate("abcd") == 2

    def test_ceil_rounding(self) -> None:
        from nerdvana_cli.core.context.token_estimator import CharEstimator
        est = CharEstimator(avg_chars_per_token=4)
        # 5 chars / 4 = 1.25 → ceil = 2
        assert est.estimate("hello") == 2


# ---------------------------------------------------------------------------
# TiktokenEstimator
# ---------------------------------------------------------------------------

class TestTiktokenEstimator:
    def test_import_and_estimate(self) -> None:
        """Should work whether or not tiktoken is installed."""
        from nerdvana_cli.core.context.token_estimator import TiktokenEstimator
        est = TiktokenEstimator(model="gpt-4o")
        result = est.estimate("Hello, world!")
        assert isinstance(result, int)
        assert result > 0

    def test_empty_string(self) -> None:
        from nerdvana_cli.core.context.token_estimator import TiktokenEstimator
        est = TiktokenEstimator()
        assert est.estimate("") == 0

    def test_unknown_model_fallback(self) -> None:
        """Unknown model should not raise — uses cl100k_base or CharEstimator."""
        from nerdvana_cli.core.context.token_estimator import TiktokenEstimator
        est = TiktokenEstimator(model="nonexistent-model-xyz")
        result = est.estimate("test input")
        assert result > 0


# ---------------------------------------------------------------------------
# AnthropicExactEstimator
# ---------------------------------------------------------------------------

class TestAnthropicExactEstimator:
    def test_no_key_uses_fallback(self) -> None:
        """Without API key, should fall back to CharEstimator gracefully."""
        from nerdvana_cli.core.context.token_estimator import AnthropicExactEstimator
        est = AnthropicExactEstimator(api_key="")
        result = est.estimate("Some text here")
        assert isinstance(result, int)
        assert result > 0

    def test_empty_string_no_key(self) -> None:
        from nerdvana_cli.core.context.token_estimator import AnthropicExactEstimator
        est = AnthropicExactEstimator(api_key="")
        assert est.estimate("") == 0


# ---------------------------------------------------------------------------
# TokenEstimatorRegistry
# ---------------------------------------------------------------------------

class TestTokenEstimatorRegistry:
    def test_none_provider_returns_char(self) -> None:
        from nerdvana_cli.core.context.token_estimator import CharEstimator, TokenEstimatorRegistry
        est = TokenEstimatorRegistry.get_for(None)
        assert isinstance(est, CharEstimator)

    def test_anthropic_provider(self) -> None:
        from nerdvana_cli.core.context.token_estimator import AnthropicExactEstimator, TokenEstimatorRegistry
        est = TokenEstimatorRegistry.get_for("anthropic", api_key="")
        assert isinstance(est, AnthropicExactEstimator)

    def test_openai_provider(self) -> None:
        from nerdvana_cli.core.context.token_estimator import TiktokenEstimator, TokenEstimatorRegistry
        est = TokenEstimatorRegistry.get_for("openai")
        assert isinstance(est, TiktokenEstimator)

    def test_groq_provider(self) -> None:
        from nerdvana_cli.core.context.token_estimator import TiktokenEstimator, TokenEstimatorRegistry
        est = TokenEstimatorRegistry.get_for("groq")
        assert isinstance(est, TiktokenEstimator)

    def test_ollama_provider_returns_char(self) -> None:
        from nerdvana_cli.core.context.token_estimator import CharEstimator, TokenEstimatorRegistry
        est = TokenEstimatorRegistry.get_for("ollama")
        assert isinstance(est, CharEstimator)

    def test_unknown_provider_returns_char(self) -> None:
        from nerdvana_cli.core.context.token_estimator import CharEstimator, TokenEstimatorRegistry
        est = TokenEstimatorRegistry.get_for("unknown-provider")
        assert isinstance(est, CharEstimator)

    def test_case_insensitive(self) -> None:
        from nerdvana_cli.core.context.token_estimator import TiktokenEstimator, TokenEstimatorRegistry
        est = TokenEstimatorRegistry.get_for("OpenAI")
        assert isinstance(est, TiktokenEstimator)

    def test_mistral_provider(self) -> None:
        from nerdvana_cli.core.context.token_estimator import TiktokenEstimator, TokenEstimatorRegistry
        est = TokenEstimatorRegistry.get_for("mistral")
        assert isinstance(est, TiktokenEstimator)


# ---------------------------------------------------------------------------
# estimate_tokens module-level convenience
# ---------------------------------------------------------------------------

class TestEstimateTokensFunction:
    def test_no_provider(self) -> None:
        from nerdvana_cli.core.context.token_estimator import estimate_tokens
        result = estimate_tokens("hello world")
        assert result > 0

    def test_with_provider(self) -> None:
        from nerdvana_cli.core.context.token_estimator import estimate_tokens
        result = estimate_tokens("hello world", provider="openai")
        assert result > 0


# ---------------------------------------------------------------------------
# The default approximation against known tokenizer counts
# ---------------------------------------------------------------------------

# Token counts of tiktoken 0.12 for these texts, as (text, cl100k_base, o200k_base). The tolerance of
# the default approximation: never under cl100k_base, the less efficient of the two on Korean, and
# never over twice o200k_base.
KNOWN_COUNTS: dict[str, tuple[str, int, int]] = {
    "english":     ("The agent reads the file, edits the function and runs the tests again until they pass.", 18, 18),
    "korean":      ("에이전트가 파일을 읽고 함수를 고친 뒤 시험이 통과할 때까지 다시 실행한다.", 36, 25),
    "mixed":       ("설정 파일 nerdvana.yml 의 model.provider 값을 openai 로 바꾸고 `nerdvana run` 을 다시 실행한다.", 33, 27),
    "korean_long": ("한국어 문장은 영어보다 글자당 토큰이 많이 든다. 압축 임계값을 계산할 때 이 차이를 무시하면 문맥 창이 넘친다. " * 3, 196, 121),
}


@pytest.mark.parametrize("name", sorted(KNOWN_COUNTS))
def test_the_default_stays_within_the_tolerance_of_the_known_counts(name: str) -> None:
    text, cl100k, o200k = KNOWN_COUNTS[name]
    assert cl100k <= approx_tokens(text) <= 2 * o200k


def test_the_default_figures_for_the_samples() -> None:
    assert [approx_tokens(KNOWN_COUNTS[name][0]) for name in ("english", "korean", "mixed", "korean_long")] == [22, 50, 42, 218]


def test_hangul_counts_one_and_a_half_ascii_a_quarter_and_other_scripts_one() -> None:
    assert CharEstimator().estimate("가나다라") == 6
    assert CharEstimator().estimate("代理读取") == 4
    assert CharEstimator().estimate("abcd가") == 3


@pytest.mark.parametrize("name", sorted(KNOWN_COUNTS))
def test_the_known_counts_are_what_tiktoken_says(name: str) -> None:
    tiktoken = pytest.importorskip("tiktoken")
    try:
        encodings = [tiktoken.get_encoding("cl100k_base"), tiktoken.get_encoding("o200k_base")]
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"tiktoken encodings unavailable: {exc}")
    text, cl100k, o200k = KNOWN_COUNTS[name]
    assert [len(encoding.encode(text)) for encoding in encodings] == [cl100k, o200k]


def test_openai_compatible_sessions_count_with_tiktoken() -> None:
    assert isinstance(estimator_for("openai", "gpt-4o"), TiktokenEstimator)
    assert estimator_for("openai", "gpt-4o") is estimator_for("openai", "gpt-4o")


@pytest.mark.parametrize("provider", [None, "", "anthropic", "ollama"])
def test_other_sessions_count_with_the_local_approximation(provider: str | None) -> None:
    assert isinstance(estimator_for(provider), CharEstimator)


def test_the_session_provider_estimates_unreported_usage_with_the_session_estimator() -> None:
    from nerdvana_cli.core.config.settings import ModelConfig
    from nerdvana_cli.core.context.loop_context import new_provider
    provider = new_provider(ModelConfig(provider="openai", model="gpt-4o", api_key="k"))
    assert provider.config.count_tokens == estimator_for("openai", "gpt-4o").estimate


class _Fixed(CharEstimator):
    """Counts every text as seven tokens."""

    def estimate(self, text: str) -> int:
        return 7


def test_the_context_budget_counts_with_its_estimator() -> None:
    budget = ContextBudget(_Fixed())
    assert budget.current([Message(role=Role.USER, content="가" * 100), Message(role=Role.ASSISTANT, content="x")]) == 14


def test_compact_messages_counts_korean_with_the_default_approximation() -> None:
    messages  = [Message(role=Role.USER, content="가" * 100) for _ in range(14)]
    compacted = compact_messages(messages, 1_600)
    assert len(compacted) == 11 and "4 earlier messages removed" in compacted[0].content


def test_compact_messages_counts_with_the_estimator_it_is_given() -> None:
    messages  = [Message(role=Role.USER, content="x") for _ in range(14)]
    compacted = compact_messages(messages, 80, _Fixed())
    assert compacted[0] is messages[0] and "3 earlier messages removed" in compacted[1].content and len(compacted) == 12
