from nerdvana_cli.providers.base import resolve_context_window, ProviderName


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
