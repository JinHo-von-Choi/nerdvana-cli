"""Tests for M3: setup wizard default provider index from saved config."""

from __future__ import annotations

from nerdvana_cli.core.setup import _resolve_default_provider_index

PROVIDERS_DISPLAY = [
    ("anthropic", "Anthropic (Claude)", "claude-sonnet-4-20250514", "ANTHROPIC_API_KEY"),
    ("openai", "OpenAI (GPT/o-series)", "gpt-4.1", "OPENAI_API_KEY"),
    ("gemini", "Google Gemini", "gemini-2.5-flash", "GEMINI_API_KEY"),
    ("groq", "Groq (Fast LLM)", "llama-3.3-70b-versatile", "GROQ_API_KEY"),
    ("openrouter", "OpenRouter (Many)", "anthropic/claude-sonnet-4", "OPENROUTER_API_KEY"),
    ("xai", "xAI (Grok)", "grok-3", "XAI_API_KEY"),
    ("featherless", "Featherless AI", "featherless-llama-3-70b", "FEATHERLESS_API_KEY"),
    ("xiaomi_mimo", "Xiaomi MiMo", "mimo-v2.5-pro", "MIMO_API_KEY"),
    ("ollama", "Ollama (Local)", "qwen3", "(no key needed)"),
    ("vllm", "vLLM (Local)", "Qwen/Qwen3-32B", "(no key needed)"),
    ("deepseek", "DeepSeek", "deepseek-chat", "DEEPSEEK_API_KEY"),
    ("mistral", "Mistral AI", "mistral-medium-latest", "MISTRAL_API_KEY"),
    ("cohere", "Cohere", "command-r-plus", "CO_API_KEY"),
    ("together", "Together AI", "meta-llama/Llama-3.3-70B-Instruct-Turbo", "TOGETHER_API_KEY"),
    ("zai", "Z.AI (GLM)", "glm-4.7", "ZHIPUAI_API_KEY"),
]


# ---------------------------------------------------------------------------
# Pure helper tests
# ---------------------------------------------------------------------------

def test_resolve_known_provider_first():
    assert _resolve_default_provider_index("anthropic", PROVIDERS_DISPLAY) == "1"


def test_resolve_known_provider_middle():
    # "dashscope" is not in the list above, use "openai" at index 2
    assert _resolve_default_provider_index("openai", PROVIDERS_DISPLAY) == "2"


def test_resolve_known_provider_last():
    assert _resolve_default_provider_index("zai", PROVIDERS_DISPLAY) == "15"


def test_resolve_unknown_provider_defaults_to_1():
    assert _resolve_default_provider_index("unknown_provider", PROVIDERS_DISPLAY) == "1"


def test_resolve_empty_saved_provider_defaults_to_1():
    assert _resolve_default_provider_index("", PROVIDERS_DISPLAY) == "1"


def test_resolve_empty_providers_list_defaults_to_1():
    assert _resolve_default_provider_index("anthropic", []) == "1"


# ---------------------------------------------------------------------------
# Integration: load_config feeds saved provider correctly
# ---------------------------------------------------------------------------

def test_wizard_default_uses_saved_provider(tmp_path, monkeypatch):
    """_resolve_default_provider_index with dashscope saved returns correct index."""
    # dashscope is not in PROVIDERS_DISPLAY — should fall back to "1"
    result = _resolve_default_provider_index("dashscope", PROVIDERS_DISPLAY)
    assert result == "1"


def test_wizard_default_uses_saved_provider_xiaomi(tmp_path):
    """saved provider=xiaomi_mimo → index 8."""
    result = _resolve_default_provider_index("xiaomi_mimo", PROVIDERS_DISPLAY)
    assert result == "8"


def test_wizard_default_uses_saved_provider_openrouter():
    """saved provider=openrouter → index 5."""
    result = _resolve_default_provider_index("openrouter", PROVIDERS_DISPLAY)
    assert result == "5"


def test_wizard_default_no_saved_config(tmp_path, monkeypatch):
    """When no config file exists, saved_provider is empty, default_idx is '1'."""

    from nerdvana_cli.core.setup import load_config

    cfg_path = tmp_path / "nerdvana.yml"
    monkeypatch.setattr(
        "nerdvana_cli.core.paths.user_config_path",
        lambda: cfg_path,
    )

    existing_cfg = load_config()
    saved_provider = (existing_cfg.get("model") or {}).get("provider", "")
    result = _resolve_default_provider_index(saved_provider, PROVIDERS_DISPLAY)
    assert result == "1"


def test_wizard_default_with_saved_provider_in_config(tmp_path, monkeypatch):
    """When config has provider=groq, default_idx resolves to '4'."""
    import yaml

    cfg_path = tmp_path / "nerdvana.yml"
    cfg_path.write_text(yaml.dump({"model": {"provider": "groq", "model": "llama-3.3-70b-versatile"}}))

    monkeypatch.setattr(
        "nerdvana_cli.core.paths.user_config_path",
        lambda: cfg_path,
    )

    from nerdvana_cli.core.setup import load_config
    existing_cfg = load_config()
    saved_provider = (existing_cfg.get("model") or {}).get("provider", "")
    result = _resolve_default_provider_index(saved_provider, PROVIDERS_DISPLAY)
    assert result == "4"
