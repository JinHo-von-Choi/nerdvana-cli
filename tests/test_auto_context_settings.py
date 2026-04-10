import yaml
import pytest


def test_auto_applies_provider_context(tmp_path):
    config = {"model": {"provider": "groq", "model": "llama-3.3-70b-versatile", "api_key": "test"}}
    path = tmp_path / "config.yml"
    path.write_text(yaml.dump(config))
    from nerdvana_cli.core.settings import NerdvanaSettings
    s = NerdvanaSettings.load(config_path=str(path))
    assert s.session.max_context_tokens == 32_768


def test_explicit_yaml_overrides(tmp_path):
    config = {"model": {"provider": "anthropic", "model": "claude-sonnet-4-20250514", "api_key": "test"}, "session": {"max_context_tokens": 100_000}}
    path = tmp_path / "config.yml"
    path.write_text(yaml.dump(config))
    from nerdvana_cli.core.settings import NerdvanaSettings
    s = NerdvanaSettings.load(config_path=str(path))
    assert s.session.max_context_tokens == 100_000


def test_openai_gpt41_gets_1m(tmp_path):
    config = {"model": {"provider": "openai", "model": "gpt-4.1", "api_key": "test"}}
    path = tmp_path / "config.yml"
    path.write_text(yaml.dump(config))
    from nerdvana_cli.core.settings import NerdvanaSettings
    s = NerdvanaSettings.load(config_path=str(path))
    assert s.session.max_context_tokens == 1_048_576


def test_session_config_compact_max_failures_default():
    from nerdvana_cli.core.settings import SessionConfig
    assert SessionConfig().compact_max_failures == 3
