"""Tests for model fallback chain on HTTP errors."""
from nerdvana_cli.core.agent_loop import _is_retryable_error
from nerdvana_cli.core.settings import ModelConfig


def test_model_config_has_fallback() -> None:
    cfg = ModelConfig()
    assert hasattr(cfg, "fallback_models")
    assert isinstance(cfg.fallback_models, list)


def test_model_config_fallback_from_dict() -> None:
    cfg = ModelConfig(
        model="claude-opus-4-6",
        fallback_models=["claude-sonnet-4-6", "openai/gpt-4.1"],
    )
    assert cfg.fallback_models == ["claude-sonnet-4-6", "openai/gpt-4.1"]


def test_is_retryable_http_error() -> None:
    assert _is_retryable_error("HTTP 429 Too Many Requests") is True
    assert _is_retryable_error("status 529") is True
    assert _is_retryable_error("503 Service Unavailable") is True
    assert _is_retryable_error("Connection timeout") is True
    assert _is_retryable_error("invalid api key") is False
    assert _is_retryable_error("model not found") is False
