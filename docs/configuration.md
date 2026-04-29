# Configuration Reference

NerdVana CLI reads configuration from, in order of decreasing priority:

1. Command-line flags (`--config`, `--provider`, `--model`, `--max-tokens`, `--cwd`, `--verbose`)
2. Environment variables (prefix `NERDVANA_`, plus provider-specific API keys)
3. Config file from `--config <path>`
4. `$NERDVANA_CONFIG` environment variable
5. `./nerdvana.yml` (current working directory)
6. `./nerdvana.yaml` (current working directory)
7. `~/.config/nerdvana-cli/config.yml`

## Environment variables

| Variable | Purpose |
|----------|---------|
| `NERDVANA_PROVIDER` | Provider name override |
| `NERDVANA_MODEL` | Model name override |
| `NERDVANA_MAX_TOKENS` | Max tokens per response |
| `NERDVANA_CONFIG` | Path to YAML config file |
| `ANTHROPIC_API_KEY` | Anthropic Claude |
| `OPENAI_API_KEY` | OpenAI (also used by vLLM and Ollama as fallback) |
| `GEMINI_API_KEY` | Google Gemini |
| `GROQ_API_KEY` | Groq |
| `OPENROUTER_API_KEY` | OpenRouter |
| `XAI_API_KEY` | xAI (Grok) |
| `OLLAMA_API_KEY` | Ollama (`OPENAI_API_KEY` accepted as fallback) |
| `VLLM_API_KEY` | vLLM (`OPENAI_API_KEY` accepted as fallback) |
| `DEEPSEEK_API_KEY` | DeepSeek |
| `MISTRAL_API_KEY` | Mistral |
| `CO_API_KEY` | Cohere |
| `TOGETHER_API_KEY` | Together AI |
| `ZHIPUAI_API_KEY` | ZAI (GLM) |
| `FEATHERLESS_API_KEY` | Featherless AI |
| `MIMO_API_KEY` | Xiaomi MiMo (`XIAOMI_API_KEY` accepted as fallback) |
| `MOONSHOT_API_KEY` | Moonshot AI (Kimi) — `KIMI_API_KEY` accepted as fallback |
| `DASHSCOPE_API_KEY` | Alibaba DashScope (Qwen) — `ALIBABA_API_KEY` accepted as fallback |
| `MINIMAX_API_KEY` | MiniMax |
| `PERPLEXITY_API_KEY` | Perplexity (`PPLX_API_KEY` accepted as fallback) |
| `FIREWORKS_API_KEY` | Fireworks AI |
| `CEREBRAS_API_KEY` | Cerebras |

## `nerdvana.yml` schema

### `model` (ModelConfig)

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `provider` | str | `""` (auto-detect from model name) | One of `anthropic`, `openai`, `gemini`, `groq`, etc. |
| `model` | str | `"claude-sonnet-4-20250514"` | Model identifier |
| `api_key` | str | `""` (use env var) | API key override |
| `base_url` | str | `""` (provider default) | Override API endpoint (Ollama, vLLM, self-hosted) |
| `max_tokens` | int | `8192` | Max tokens per response |
| `temperature` | float | `1.0` | Sampling temperature |
| `fallback_models` | list[str] | `[]` | Phase C: models to try on 429/529/503/timeout errors |
| `extended_thinking` | bool | `false` | Phase C: enabled automatically by `ultrawork`/`ulw` keywords |
| `thinking_budget` | int | `8192` | Phase C: max tokens for extended thinking |

### `permissions` (PermissionConfig)

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `mode` | str | `"default"` | `default`, `accept-edits`, `bypass`, `plan` |
| `always_allow` | list[str] | `[]` | Tool names to always allow without prompt |
| `always_deny` | list[str] | `[]` | Tool names to always block |

### `session` (SessionConfig)

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `persist` | bool | `true` | Save JSONL transcripts |
| `max_turns` | int | `200` | Agent loop turn limit |
| `max_context_tokens` | int | `180000` | Auto-resolved per model; overridable |
| `compact_threshold` | float | `0.8` | Fraction of max_context_tokens that triggers compaction |
| `compact_max_failures` | int | `3` | AI compaction circuit breaker |
| `planning_gate` | bool | `false` | Spawn Plan subagent on complex prompts |
| `default_context` | str | `"standalone"` | Default runtime context profile name |
| `default_mode` | str | `"interactive"` | Default runtime mode name (`interactive`, `planning`, etc.) |

### `parism` (ParismConfig)

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `enabled` | bool | `true` | Enable Parism structured shell output |
| `config_path` | str | `""` | Parism config file path |
| `format` | str | `"json"` | Output format |
| `fallback_to_bash` | bool | `true` | Fall back to Bash if Parism unavailable |

### `hooks` (HookConfig)

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `session_start` | list[str] | `["builtin:context_injection"]` | Hook handler IDs |
| `before_tool` | list[str] | `[]` | |
| `after_tool` | list[str] | `[]` | |

Note: Built-in recovery hooks (`context_limit_recovery`, `json_parse_recovery`, `ralph_loop_check`) are auto-registered in `AgentLoop.__init__` and are not listed here.

### `checkpoint` (CheckpointConfig)

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `enabled` | bool | `true` | Automatically save session checkpoints |
| `per_session_max` | int | `50` | Maximum number of checkpoints retained per session |

## Full example

```yaml
model:
  provider: anthropic
  model: claude-sonnet-4-20250514
  max_tokens: 8192
  temperature: 1.0
  fallback_models:
    - claude-sonnet-4-20250514
    - openai/gpt-4.1
    - gemini-2.5-flash
  extended_thinking: false
  thinking_budget: 8192

permissions:
  mode: default
  always_allow:
    - FileRead
    - Glob
    - Grep
  always_deny: []

session:
  persist: true
  max_turns: 200
  max_context_tokens: 180000
  compact_threshold: 0.8
  compact_max_failures: 3
  planning_gate: true        # opt-in
  default_context: standalone
  default_mode: interactive

parism:
  enabled: true
  fallback_to_bash: true

checkpoint:
  enabled: true
  per_session_max: 50
```
