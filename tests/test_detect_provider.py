"""Tests for detect_provider — the single source of truth."""
from nerdvana_cli.providers.base import ProviderName, detect_provider


class TestDetectProvider:
    def test_claude_returns_anthropic(self):
        assert detect_provider("claude-sonnet-4-20250514") == ProviderName.ANTHROPIC

    def test_gpt_returns_openai(self):
        assert detect_provider("gpt-4.1") == ProviderName.OPENAI

    def test_o3_returns_openai(self):
        assert detect_provider("o3-mini") == ProviderName.OPENAI

    def test_gemini_returns_gemini(self):
        assert detect_provider("gemini-2.5-flash") == ProviderName.GEMINI

    def test_llama_returns_groq(self):
        assert detect_provider("llama-3.3-70b-versatile") == ProviderName.GROQ

    def test_deepseek_returns_deepseek(self):
        assert detect_provider("deepseek-chat") == ProviderName.DEEPSEEK

    def test_mistral_returns_mistral(self):
        assert detect_provider("mistral-medium-latest") == ProviderName.MISTRAL

    def test_grok_returns_xai(self):
        assert detect_provider("grok-3") == ProviderName.XAI

    def test_command_returns_cohere(self):
        assert detect_provider("command-r-plus") == ProviderName.COHERE

    def test_glm_returns_zai(self):
        assert detect_provider("glm-4.7") == ProviderName.ZAI

    def test_unknown_model_defaults_to_anthropic(self):
        assert detect_provider("some-random-model") == ProviderName.ANTHROPIC

    def test_case_insensitive(self):
        assert detect_provider("Claude-Opus-4") == ProviderName.ANTHROPIC
        assert detect_provider("GPT-4.1") == ProviderName.OPENAI

    # Moonshot AI (Kimi)
    def test_kimi_k2_instruct_returns_moonshot(self):
        assert detect_provider("kimi-k2-instruct") == ProviderName.MOONSHOT

    def test_kimi_latest_returns_moonshot(self):
        assert detect_provider("kimi-latest") == ProviderName.MOONSHOT

    def test_moonshot_v1_128k_returns_moonshot(self):
        assert detect_provider("moonshot-v1-128k") == ProviderName.MOONSHOT

    def test_moonshot_v1_32k_returns_moonshot(self):
        assert detect_provider("moonshot-v1-32k") == ProviderName.MOONSHOT

    # Alibaba DashScope (Qwen Cloud)
    def test_qwen_max_returns_dashscope(self):
        assert detect_provider("qwen-max") == ProviderName.DASHSCOPE

    def test_qwen_plus_returns_dashscope(self):
        assert detect_provider("qwen-plus") == ProviderName.DASHSCOPE

    def test_qwen_turbo_returns_dashscope(self):
        assert detect_provider("qwen-turbo") == ProviderName.DASHSCOPE

    def test_qwen_vl_max_returns_dashscope(self):
        assert detect_provider("qwen-vl-max") == ProviderName.DASHSCOPE

    def test_qwen3_coder_plus_returns_dashscope(self):
        assert detect_provider("qwen3-coder-plus") == ProviderName.DASHSCOPE

    def test_qwq_32b_preview_returns_dashscope(self):
        assert detect_provider("qwq-32b-preview") == ProviderName.DASHSCOPE

    # Regression: Groq models must not be captured by Moonshot/DashScope
    def test_llama_33_70b_versatile_returns_groq(self):
        assert detect_provider("llama-3.3-70b-versatile") == ProviderName.GROQ

    def test_llama_31_70b_instruct_returns_groq(self):
        assert detect_provider("llama-3.1-70b-instruct") == ProviderName.GROQ

    # Regression: vLLM-style slash-namespaced Qwen falls through to default
    def test_qwen_slash_namespace_returns_anthropic(self):
        assert detect_provider("Qwen/Qwen3-32B") == ProviderName.ANTHROPIC
