# Live Provider Smoke Tests

Author: 최진호
Created: 2026-05-13

Live tests in `tests/live/` exercise every supported AI provider end-to-end. They
hit real APIs, consume tokens, and may incur cost — unlike unit tests they are
**skipped automatically when the required environment variable is absent**, so
they never break a developer machine that lacks credentials.

This document is the canonical reference for running them locally and in CI.

---

## Provider → environment variable matrix

| Provider | Primary env var | Alternative | Default model | Notes |
|-|-|-|-|-|
| Anthropic | `ANTHROPIC_API_KEY` | — | `claude-sonnet-4-20250514` | metered |
| OpenAI | `OPENAI_API_KEY` | — | `gpt-4.1` | metered |
| Google Gemini | `GEMINI_API_KEY` | `GOOGLE_API_KEY` | `gemini-2.5-flash` | metered (free tier available) |
| Groq | `GROQ_API_KEY` | — | `llama-3.3-70b-versatile` | free tier with rate limits |
| OpenRouter | `OPENROUTER_API_KEY` | — | `anthropic/claude-sonnet-4` | metered (passthrough) |
| xAI (Grok) | `XAI_API_KEY` | — | `grok-3` | metered |
| Ollama | `OLLAMA_API_KEY` | — | `qwen3` | self-hosted; defaults to `http://localhost:11434/v1` |
| vLLM | `VLLM_API_KEY` | `OPENAI_API_KEY` | `Qwen/Qwen3-32B` | self-hosted; `http://localhost:8000/v1` |
| DeepSeek | `DEEPSEEK_API_KEY` | — | `deepseek-chat` | metered |
| Mistral | `MISTRAL_API_KEY` | — | `mistral-medium-latest` | metered |
| Cohere | `CO_API_KEY` | `COHERE_API_KEY` | `command-r-plus` | metered |
| Together AI | `TOGETHER_API_KEY` | — | `meta-llama/Llama-3.3-70B-Instruct-Turbo` | metered |
| ZAI (GLM) | `ZHIPUAI_API_KEY` | `ZAI_API_KEY` | `glm-4.7` | metered |
| Featherless AI | `FEATHERLESS_API_KEY` | — | `featherless-llama-3-70b` | non-streaming on standard endpoints |
| Xiaomi MiMo | `MIMO_API_KEY` | `XIAOMI_API_KEY` | `mimo-v2.5-pro` | 1M context window |
| Moonshot AI (Kimi) | `MOONSHOT_API_KEY` | `KIMI_API_KEY` | `kimi-k2-instruct` | metered |
| Alibaba DashScope (Qwen) | `DASHSCOPE_API_KEY` | `ALIBABA_API_KEY` | `qwen3-coder-plus` | 1M context window |
| MiniMax | `MINIMAX_API_KEY` | — | `MiniMax-M2` | 1M context window |
| Perplexity | `PERPLEXITY_API_KEY` | `PPLX_API_KEY` | `sonar-pro` | web-search-augmented; **tool calling not supported** |
| Fireworks AI | `FIREWORKS_API_KEY` | — | `accounts/fireworks/models/llama-v3p3-70b-instruct` | metered |
| Cerebras | `CEREBRAS_API_KEY` | — | `llama-3.3-70b` | metered |

Each live test file is named `tests/live/test_<provider>_smoke.py` and uses the
`@pytest.mark.live` marker. The `tests/live/conftest.py` fixtures inspect the
matching env var and call `pytest.skip(...)` when the key is missing, so a
machine with only one or two keys gracefully runs just those providers.

---

## Running locally

Default unit-test command (live tests excluded automatically):

```bash
uv run pytest -m "not lsp_integration and not live" -q
```

Run **all** live providers for which you have keys:

```bash
uv run pytest -m live
```

Run a **single provider** smoke test:

```bash
ANTHROPIC_API_KEY=sk-ant-... uv run pytest tests/live/test_anthropic_smoke.py -v
```

Run multiple specific providers in one go (substring match via `-k`):

```bash
uv run pytest -m live -k "anthropic or openai or gemini" -v
```

Confirm which live tests **would** run with your current environment without
actually calling the APIs:

```bash
uv run pytest -m live --collect-only -q
```

Skipped tests with a missing-key reason will appear as `SKIPPED [reason]`.

---

## Cost-aware execution

Live tests issue real, billable API calls on metered providers. Recommendations:

- **First-time setup**: run one provider at a time with a small model and a single
  test (`pytest tests/live/test_<x>_smoke.py::test_basic_completion`) to validate
  credentials before bulk runs.
- **Anthropic / OpenAI / xAI / OpenRouter / Mistral / Cohere / Fireworks / Cerebras /
  DashScope / MiniMax / Moonshot**: each smoke run typically consumes < 500 tokens,
  costing fractions of a cent — but multiplied by 21 providers and frequent CI
  reruns this adds up. Prefer running the suite manually, not in `pre-commit`.
- **Free tiers**: Groq, Gemini, OpenRouter (some models), and Perplexity expose
  free tiers with rate limits. Hitting the limit returns HTTP 429 — tests are
  expected to skip or xfail rather than fail loudly.
- **Self-hosted (Ollama / vLLM)**: zero per-call cost but require the respective
  server running and reachable at the default base URL. Override with
  `OLLAMA_BASE_URL` / `VLLM_BASE_URL` if non-default.

To avoid surprise spending, never set `*_API_KEY` env vars globally in shell
profiles. Prefer per-invocation prefixing, `.envrc` (direnv), or a sourced
`set_keys.sh` that you keep outside the repo.

---

## CI integration

The `.github/workflows/live_smoke.yml` workflow runs live tests on `main` and
`release/*` branches. Required organisation secrets correspond exactly to the
**Primary env var** column above; alternative env vars are accepted as fallbacks.

A live test that fails on CI almost always means one of:

1. The provider's free-tier rate limit was hit (transient — rerun).
2. The provider rotated API host, model id, or response schema (real bug —
   investigate the `tests/live/test_<provider>_smoke.py` file).
3. The org secret was rotated or expired (admin action required).

CI workers skip providers without secrets — this is by design so individual
contributors can ship without owning every API key.

---

## Adding a live test for a new provider

When following the new-provider checklist in [CONTRIBUTING.md](../CONTRIBUTING.md),
add `tests/live/test_<provider>_smoke.py` containing:

1. A `@pytest.mark.live` marker.
2. A skip guard (or fixture from `conftest.py`) that checks the env var and calls
   `pytest.skip("<PROVIDER>_API_KEY not set")` when absent.
3. At least one `test_basic_completion` that streams a short prompt and asserts
   non-empty content.
4. A tool-calling test (skip if provider does not support tools — see Perplexity).

Update the matrix table above in the same PR so this document stays the single
source of truth.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|-|-|-|
| `pytest.skip: <PROVIDER>_API_KEY not set` | env var missing | export the key for this shell only |
| HTTP 401 | invalid/expired key | regenerate key from provider dashboard |
| HTTP 429 | rate limit | wait, or switch to a paid tier |
| HTTP 5xx | provider outage | retry, check provider status page |
| `httpx.ConnectError` for Ollama/vLLM | server not running | start the server; verify base URL |
| Streaming returns empty deltas | model deprecated | check provider's current model catalog and update the default in `providers/base.py:DEFAULT_MODELS` |
