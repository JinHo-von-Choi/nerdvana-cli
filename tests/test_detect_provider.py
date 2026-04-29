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

    # MiniMax
    def test_minimax_m2_returns_minimax(self):
        assert detect_provider("MiniMax-M2") == ProviderName.MINIMAX

    def test_minimax_m2_lower_returns_minimax(self):
        assert detect_provider("minimax-m2") == ProviderName.MINIMAX

    def test_minimax_abab_returns_minimax(self):
        assert detect_provider("abab6.5s-chat") == ProviderName.MINIMAX

    # Perplexity
    def test_sonar_pro_returns_perplexity(self):
        assert detect_provider("sonar-pro") == ProviderName.PERPLEXITY

    def test_sonar_reasoning_pro_returns_perplexity(self):
        assert detect_provider("sonar-reasoning-pro") == ProviderName.PERPLEXITY

    def test_pplx_7b_online_returns_perplexity(self):
        assert detect_provider("pplx-7b-online") == ProviderName.PERPLEXITY

    # Fireworks AI
    def test_fireworks_llama_returns_fireworks(self):
        assert detect_provider("accounts/fireworks/models/llama-v3p3-70b-instruct") == ProviderName.FIREWORKS

    def test_fireworks_qwen_returns_fireworks(self):
        assert detect_provider("accounts/fireworks/models/qwen2p5-72b-instruct") == ProviderName.FIREWORKS

    # Cerebras — exact catalog matching
    def test_cerebras_llama33_70b_returns_cerebras(self):
        assert detect_provider("llama-3.3-70b") == ProviderName.CEREBRAS

    def test_cerebras_llama31_8b_returns_cerebras(self):
        assert detect_provider("llama-3.1-8b") == ProviderName.CEREBRAS

    def test_cerebras_llama4_scout_returns_cerebras(self):
        assert detect_provider("llama-4-scout-17b-16e-instruct") == ProviderName.CEREBRAS

    def test_cerebras_qwen3_32b_returns_cerebras(self):
        assert detect_provider("qwen-3-32b") == ProviderName.CEREBRAS

    def test_cerebras_qwen3_235b_returns_cerebras(self):
        assert detect_provider("qwen-3-235b-a22b-instruct-2507") == ProviderName.CEREBRAS

    # Regression: Groq suffix models must not be captured by Cerebras
    def test_llama33_70b_versatile_not_cerebras(self):
        assert detect_provider("llama-3.3-70b-versatile") == ProviderName.GROQ

    def test_llama31_8b_instant_not_cerebras(self):
        assert detect_provider("llama-3.1-8b-instant") == ProviderName.GROQ

    def test_mixtral_returns_groq(self):
        assert detect_provider("mixtral-8x7b-32768") == ProviderName.GROQ

    # Regression: Tier 1 routing preserved
    def test_kimi_k2_instruct_still_moonshot(self):
        assert detect_provider("kimi-k2-instruct") == ProviderName.MOONSHOT

    def test_qwen_max_still_dashscope(self):
        assert detect_provider("qwen-max") == ProviderName.DASHSCOPE
