from nerdvana_cli.providers.base import ProviderName, resolve_context_window


def test_anthropic_default():
    assert resolve_context_window(ProviderName.ANTHROPIC, "claude-sonnet-4-20250514") == 200_000


def test_openai_gpt4o():
    assert resolve_context_window(ProviderName.OPENAI, "gpt-4o") == 128_000


def test_openai_gpt41():
    assert resolve_context_window(ProviderName.OPENAI, "gpt-4.1") == 1_048_576


def test_openai_o3():
    assert resolve_context_window(ProviderName.OPENAI, "o3") == 200_000


def test_groq_default():
    assert resolve_context_window(ProviderName.GROQ, "llama-3.3-70b-versatile") == 32_768


def test_deepseek():
    assert resolve_context_window(ProviderName.DEEPSEEK, "deepseek-reasoner") == 65_536


def test_unknown_model_falls_back():
    assert resolve_context_window(ProviderName.ANTHROPIC, "some-future-model") == 200_000


def test_gemini_flash():
    assert resolve_context_window(ProviderName.GEMINI, "gemini-2.5-flash") == 1_048_576


def test_claude_46_and_later_resolve_to_the_one_million_window():
    for model in ("claude-sonnet-5-5", "claude-opus-5-5", "claude-fable-5-1", "claude-sonnet-4-6", "claude-opus-4-7"):
        assert resolve_context_window(ProviderName.ANTHROPIC, model) == 1_000_000, model


def test_haiku_45_keeps_the_200k_window():
    assert resolve_context_window(ProviderName.ANTHROPIC, "claude-haiku-4-5-20251001") == 200_000


def test_default_model_resolves_in_every_table():
    """The shipped default must have a context window and a price, or cost and
    compaction silently fall back to guesses."""
    from nerdvana_cli.core.analytics import PricingTable
    from nerdvana_cli.core.settings import ModelConfig

    model = ModelConfig().model
    assert resolve_context_window(ProviderName.ANTHROPIC, model) > 0
    assert model in PricingTable().known_models("anthropic")
