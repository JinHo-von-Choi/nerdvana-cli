"""Tests for model fallback chain on HTTP errors."""
from nerdvana_cli.core.loop_hooks import LoopHookEngine
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
    # _is_retryable_error was moved to LoopHookEngine (T-0A-05)
    engine = LoopHookEngine(hooks=None, settings=None, registry=None)
    assert engine._is_retryable_error(Exception("HTTP 429 Too Many Requests")) is True
    assert engine._is_retryable_error(Exception("status 529")) is True
    assert engine._is_retryable_error(Exception("503 Service Unavailable")) is True
    assert engine._is_retryable_error(Exception("Connection timeout")) is True
    assert engine._is_retryable_error(Exception("invalid api key")) is False
    assert engine._is_retryable_error(Exception("model not found")) is False
