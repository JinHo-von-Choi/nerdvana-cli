# Configuration Reference

NerdVana CLI reads configuration from, in order of decreasing priority:

1. Command-line flags (`--config`, `--provider`, `--model`, `--max-tokens`, `--cwd`, `--verbose`)
2. Environment variables (the `NERDVANA_` names listed below, plus provider-specific API keys)
3. The config file

`NerdvanaSettings.load` walks these candidate files in order and stops at the
first one that exists:

1. the path given to `--config`
2. `$NERDVANA_CONFIG`
3. `./nerdvana.yml`
4. `./nerdvana.yaml`
5. `~/.nerdvana/config.yml`
6. `~/.config/nerdvana-cli/config.yml`, the pre-migration location, still read so an
   un-migrated install keeps working

Only that one file is read. Keys absent from it fall back to the schema
defaults below, never to a lower-priority file.

## Environment variables

| Variable | Purpose |
|----------|---------|
| `NERDVANA_CONFIG` | Path to YAML config file |
| `NERDVANA_HOME` | Install root (read-only at runtime); default `~/.nerdvana-cli` |
| `NERDVANA_DATA_HOME` | User data root; default `~/.nerdvana` |
| `NERDVANA_EXTERNAL_PROJECTS_ENABLED` | Register the external project tools (`external_projects_enabled` without a config file) |
| `NERDVANA_EXTERNAL_PROJECTS_ROOT` | Boundary root the external project tools may not escape |
| `NERDVANA_CWD` | Working directory override |
| `NERDVANA_VERBOSE` | Verbose output |
| `NERDVANA_NO_UPDATE_CHECK` | Set to `1` to disable the startup version check (same effect as `--no-update-check`) |
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
| `BRAVE_API_KEY` | Brave Search, used by the `WebSearch` tool. Without it `WebSearch` raises at call time; every other tool is unaffected. |

`provider` and `model` have no environment override. The `NERDVANA_` prefix
covers the scalar settings only: the nested blocks (`model`, `session`,
`permissions`, `parism`, `hooks`, `checkpoint`) would have to be supplied as
whole JSON documents, so use `--provider` / `--model`, the `/provider` and
`/model` REPL commands, or the config file instead.

## `nerdvana.yml` schema

### `model` (ModelConfig)

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `provider` | str | `""` (auto-detect from model name) | One of `anthropic`, `openai`, `gemini`, `groq`, etc. |
| `model` | str | `"claude-sonnet-5-5"` | Model identifier |
| `api_key` | str | `""` (use env var) | API key override |
| `base_url` | str | `""` (provider default) | Override API endpoint (Ollama, vLLM, self-hosted) |
| `max_tokens` | int | `8192` | Max tokens per response |
| `temperature` | float | `1.0` | Sampling temperature |
| `fallback_models` | list[str] | `[]` | Models tried in order when a request keeps failing with a transient error (429, 5xx, timeout) or an authentication error. `model` runs under the configured provider; `provider:model` (for example `openai:gpt-4.1`) switches provider and uses that provider's API key. A model that just failed is skipped for 60 seconds. |
| `prompt_caching` | bool | `true` | Lets the provider reuse the processed system prompt, tool list and conversation prefix between requests. Anthropic requests get explicit cache breakpoints (last tool, system prompt, last message block); OpenAI-compatible servers and Gemini cache on their own and only their cached-token counts are read. Cached reads are billed at a fraction of normal input, and `nerdvana` prices them with the per-model `cache_read_per_1m` and `cache_write_per_1m` rates. |
| `max_retries` | int | `2` | Retries of the same model, with backoff (or the server's `Retry-After`), before moving to the next fallback. Nothing is retried once part of the answer has streamed. |
| `extended_thinking` | bool | `false` | Turns on thinking for Anthropic models where it is optional (Opus 4.6 to 4.8, Sonnet 4.6 and the manual-budget models such as Haiku 4.5); the `ultrawork`/`ulw` keyword turns it on for one prompt. Claude 5 models (Fable, Opus, Sonnet) think by default, so the switch has no effect on them. Other providers are unaffected. |
| `thinking_budget` | int | `8192` | Thinking token budget for models that take a manual budget (Haiku 4.5 and earlier). Models with adaptive thinking choose their own depth and ignore it. |
| `show_thinking` | bool | `true` | Asks Anthropic models for thinking summaries (they omit them by default) and renders the thinking content from the response stream as a dim italic block above the answer. Toggled via `/thinking on|off`. |
| `reasoning_effort` | string | `` | How hard OpenAI-compatible and Gemini models reason, in the provider's own words, sent as it is: OpenAI `reasoning_effort` (`none`, `minimal`, `low`, `medium`, `high`, `xhigh`; which values a model accepts differs, and an unsupported one is refused by the API) and the Gemini thinking level (`minimal`, `low`, `medium`, `high`; any other value stops the request with an error, and which levels a model accepts also differs). Empty leaves the provider's default. Anthropic models ignore it. `nerdvana run --set model.reasoning_effort=high` sets it for one run. |

### `model_history` (dict[str, str])

| Field | Type | Default | Description |
|-|-|-|-|
| `model_history` | `dict[str, str]` | `{}` | Per-provider last-used model. Updated whenever `/model` or `/provider` switches a selection so that returning to a provider restores its previous model. Keys are provider names (lowercase, matching `ProviderName.value`); values are model identifiers. |

### `permissions` (PermissionConfig)

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `mode` | str | `"default"` | `default`, `accept-edits`, `bypass`, `plan`; mapped onto the `interactive`, `editing`, `one-shot` and `planning` mode profiles. `--approval-mode` and `session.default_mode` take precedence. |
| `always_allow` | list[str] | `[]` | Tool names (glob patterns allowed) run without asking; `Tool(pattern)` limits a rule to calls whose main argument matches: the command of `Bash` or `Parism`, the path of a file tool, the URL of `WebFetch`. `Bash(git status)` allows that command, `Bash(git diff *)` allows `git diff` with any arguments, and a command with shell operators (`;`, `&&`, `|`, `$(`, `>` and the like) never matches an allow rule that names arguments. A tool's own refusal still wins. `nerdvana approvals` suggests rules from your own answers. |
| `always_deny` | list[str] | `[]` | Tool names (glob patterns allowed) always refused, in every mode; `Tool(pattern)` limits a rule to calls whose main argument matches, and for a deny rule a command with shell operators matches like any other. |

Every tool call goes through one policy, in this order: `always_deny`, tools excluded by the active mode, the tool's own refusal, `always_allow`, then the mode's trust level (`strict` asks before any write, `balanced` asks before destructive tools and tools that require confirmation, `yolo` asks nothing). Sub-agents and background agents follow the same policy.

### `session` (SessionConfig)

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `persist` | bool | `true` | Save JSONL transcripts |
| `max_turns` | int | `200` | Agent loop turn limit |
| `max_cost_usd` | float | `0` | Stop once the estimated cost of this session's provider requests reaches this many USD (`0` = no limit). Needs a known price for the model; without one the limit is not enforced and a notice says so. Also set per run with `nerdvana run --max-cost-usd`. |
| `max_total_tokens` | int | `0` | Stop once the input and output tokens of all provider requests in the session add up to this many (`0` = no limit). Needs no price list, so it bounds spending for models the price table does not know. Also `nerdvana run --max-total-tokens`. |
| `project_doc_max_tokens` | int | `0` | Longest a project document (NIRNA.md, AGENTS.md, CLAUDE.md) may be in the system prompt, in tokens. A longer one is cut at a paragraph boundary and the notice names the file so the model can read the rest. `0` keeps every document whole. These documents are sent with every request; `nerdvana doctor` reports how much they add. |
| `subagent_budget_fraction` | float | `0.5` | With `max_cost_usd` set, each sub-agent (an `Agent` call, or one `Swarm` split between its tasks) may spend this share of what is still unspent and unpromised, stops there and returns what it has, and its spend counts against `max_cost_usd`. `0` gives sub-agents no share. See [agents.md](agents.md). |
| `defer_tools` | string | `auto` | MCP tools whose full declaration is sent only after the model loads them with `ToolSearch`: `auto` defers them once their declarations add up to more than `defer_tools_threshold` tokens, `always` defers them whatever their size, `never` declares them all. The system prompt lists deferred tools by name with one line each. Sub-agents declare every tool. See [mcp-deferred-tools.md](mcp-deferred-tools.md). |
| `defer_tools_threshold` | int | `3000` | Size, in estimated tokens, above which `defer_tools: auto` defers the MCP tools. |
| `escalation_model` | string | `` | Model to switch to, once per session, when the run shows trouble: `model` for the current provider or `provider:model` (the provider's API key must be in the environment). Empty = never. Start on a cheap model (`model.model` or an agent category) and let this one take over only if it is needed. The thinking blocks of the earlier model are dropped on the switch, and providers that cache the start of a request reread it once. |
| `escalation_signals` | map | `verify_failed: 1`, `repeat_refused: 1`, `cas_rejected: 3`, `new_diagnostics: 4` | Signal name to the number of occurrences that triggers the escalation; the signals are the ones counted in the `signals` of a run result. |
| `mask_secrets` | bool | `true` | Replace secret-looking values in the output of commands and external tools with `[REDACTED]` before the model sees it. See [secret-masking.md](secret-masking.md). |
| `mask_extra_patterns` | list | `[]` | Regular expressions whose matches are replaced as well. |
| `require_price` | bool | `false` | Refuse to run when `max_cost_usd` is set but the model has no known price, instead of continuing without a cost limit. Also `nerdvana run --require-price`. |
| `max_context_tokens` | int | `180000` | Auto-resolved per model (1M-token models resolve to 1,000,000, so compaction starts near 800,000 tokens); set a smaller value to compact earlier |
| `compact_threshold` | float | `0.8` | Fraction of max_context_tokens that triggers compaction |
| `compact_max_failures` | int | `3` | AI compaction circuit breaker |
| `planning_gate` | bool | `false` | Spawn Plan subagent on complex prompts |
| `default_context` | str | `"standalone"` | Default runtime context profile name |
| `default_mode` | str | `"interactive"` | Default runtime mode name (`interactive`, `planning`, etc.) |
| `show_activity` | bool | `true` | Show the live ActivityIndicator widget between the chat log and the input row. Toggled via `/activity on|off`. |
| `stream_idle_timeout` | float | `300.0` | Seconds a provider stream may stay silent before the request is abandoned and treated as a transient failure. `0` disables. |
| `stream_total_timeout` | float | `3600.0` | Seconds one response may take in total. `0` disables. |
| `post_edit_diagnostics` | bool | `true` | After a file or symbol edit, ask the running language server for errors and append only the errors the edit introduced to the tool result. |
| `max_parallel_agents` | int | `5` | Sub-agents (`Agent`, `Swarm`) that may run at once against one provider; the rest wait. |
| `update_check` | bool | `true` | Check GitHub Releases for a newer version on every CLI invocation. The result is cached for 24 hours at `~/.nerdvana/cache/update_check.json`. A one-line notice is printed when a newer version is available. Override priority (highest to lowest): `--no-update-check` flag > `NERDVANA_NO_UPDATE_CHECK=1` env > this setting. |

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
| `allow_project_hooks` | bool | `false` | Permit `<cwd>/.nerdvana/hooks/*.py` to run. Off by default, and an opt-in alone is not enough: each file must also match an approved SHA-256 digest. See [hooks.md](hooks.md). |

Note: Built-in recovery hooks (`context_limit_recovery`, `json_parse_recovery`, `ralph_loop_check`) are auto-registered in `AgentLoop.__init__` and are not listed here.

### Top-level keys

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `external_projects_enabled` | bool | `false` | Register `ListQueryableProjects`, `RegisterExternalProject`, and `QueryExternalProject`. These hand a registered directory to a read-capable subprocess, so the family stays unregistered until this is set. |
| `model_history` | dict[str, str] | `{}` | Per-provider last-used model, written by `/model` and `/provider`. |

### MCP server quota

Optional. When `~/.nerdvana/mcp_quota.yml` is absent, no quota enforcement is applied and existing behaviour is preserved. The file path can be overridden by passing a custom `quota_resolver=` to `NerdvanaMcpServer`.

Minimal example:

```yaml
default:
  rpm: 60
  rph: 0          # 0 = unlimited
  daily_tokens: 0
  max_concurrent: 4

tenants:
  free_tier:
    rpm: 10
    daily_tokens: 100000

roles:
  admin:
    rpm: 0
```

Schema sections: `default`, `tenants`, `roles`. Dimensions: `rpm` (requests per minute), `rph` (requests per hour), `daily_tokens`, `max_concurrent`. A value of `0` means unlimited. See [docs/mcp-quota.md](mcp-quota.md) for the full schema reference.

### `skills` (SkillsConfig)

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `include_claude_skills` | bool | `false` | Also load skills from `~/.claude/skills` and `<cwd>/.claude/skills`, one tier below the matching nerdvana skill directories. Only each skill's `SKILL.md` is read; bundled files are never run. |

### `sandbox` (SandboxConfig)

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `mode` | string | `off` | `off`, `auto` (confine where the system supports it) or `require` (refuse a command that cannot be confined). An invalid value stops startup. See [sandbox.md](sandbox.md). |
| `network` | bool | `true` | `false` also refuses TCP connections and binds from the confined command; needs Linux 6.7 (Landlock ABI 4). |
| `write_paths` | list | `[]` | Paths a confined command may write in addition to the project directory and the temporary directories. |
| `project_writable` | bool | `true` | The project directory is writable to confined commands. Agent definitions with a `write_scope` turn it off; see [agents.md](agents.md). |
| `scratch_writable` | bool | `true` | `/tmp` and the system temporary directory are writable to confined commands. |
| `edit_scope` | list | unset | Paths (relative to the project) that `FileWrite`, `FileEdit` and the symbol edit tools may change; an edit elsewhere is refused. Unset means anywhere the permissions allow. These tools run in the application, so Landlock cannot confine them. |

### `goal` (GoalConfig)

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `verify_timeout` | int | `300` | Seconds before a verification command is stopped, together with everything it started. |
| `max_attempts` | int | `5` | Failed verifications before the goal is given up and the run ends as unmet. |
| `output_tail_chars` | int | `4000` | How much of the end of a failing verification output is shown to the model. |

See [goals.md](goals.md).

### `agents` (AgentsConfig)

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `categories` | map | `{}` | Category name to model for sub-agents, written `model` or `provider:model`. An `Agent` call, a `Swarm` task or an agent definition that names a category runs on the mapped model; see [agents.md](agents.md). |

### `checkpoint` (CheckpointConfig)

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `enabled` | bool | `true` | Automatically save session checkpoints |
| `per_session_max` | int | `50` | Maximum number of checkpoints retained per session |

### Pricing maintenance

`nerdvana_cli/providers/pricing.yml` stores rates as USD per 1,000,000 tokens under the keys `input_per_1m` and `output_per_1m`, which is the unit vendors publish, so a value can be copied from a source table without conversion. An entry may also carry `cache_write_per_1m` and `cache_read_per_1m` for prompt caching; without them cached tokens are billed at `input_per_1m`. Each provider block carries a snapshot comment that records when the values were last verified. Recommended cadence: once per quarter.

Scanning for stale entries:

```
python scripts/check_pricing_freshness.py
```

Exits 0 if all snapshots are within the TTL (default 90 days, override with `NERDVANA_PRICING_TTL_DAYS`). Pass `--report-only` to suppress the non-zero exit code in CI informational runs.

Updating a provider:

```
python scripts/update_pricing.py --provider anthropic
python scripts/update_pricing.py --provider groq        # manual URL printed
```

Auto-fetch is available for `anthropic`, `openai`, and `google`. For all other providers the script prints the canonical source URL and exits 2; update the YAML manually and bump the snapshot comment date.

Snapshot comment format (first comment line inside the provider block):

```yaml
anthropic:
  # 2026-04-29 snapshot — https://www.anthropic.com/pricing
  claude-sonnet-5-5: ...
```

Provider pricing sources:

| Provider | Pricing source URL |
|-|-|
| Anthropic | https://www.anthropic.com/pricing |
| OpenAI | https://openai.com/api/pricing |
| Google (Gemini) | https://ai.google.dev/pricing |
| Groq | https://console.groq.com/docs/openai |
| OpenRouter | https://openrouter.ai/models |
| xAI (Grok) | https://x.ai/api |
| Ollama | https://ollama.com (local inference, no public pricing) |
| vLLM | https://docs.vllm.ai (local inference, no public pricing) |
| DeepSeek | https://platform.deepseek.com/api-docs/pricing |
| Mistral | https://mistral.ai/technology/ |
| Cohere | https://cohere.com/pricing |
| Together AI | https://www.together.ai/pricing |
| ZAI (GLM) | https://open.bigmodel.cn/pricing |
| Featherless AI | https://featherless.ai/pricing |
| Xiaomi MiMo | https://token-plan-sgp.xiaomimimo.com |
| Moonshot (Kimi) | https://platform.moonshot.ai/docs/pricing |
| DashScope (Qwen) | https://help.aliyun.com/document_detail/2840914.html |
| MiniMax | https://www.minimaxi.chat/document/pricing |
| Perplexity | https://docs.perplexity.ai/guides/pricing |
| Fireworks AI | https://fireworks.ai/pricing |
| Cerebras | https://inference-docs.cerebras.ai/introduction#pricing |

## Selection persistence

`/provider` and `/model` write both the active selection and the per-provider history to the config file referenced by the priority chain above. Restarting `nerdvana` reads the same file, so the last active provider, model, base URL, and per-provider history are restored without further input.

## Full example

```yaml
model:
  provider: anthropic
  model: claude-sonnet-5-5
  max_tokens: 8192
  temperature: 1.0
  fallback_models:
    - claude-haiku-4-5-20251001
    - openai:gpt-4.1
    - gemini:gemini-2.5-flash
  extended_thinking: false
  thinking_budget: 8192
  show_thinking: true
  reasoning_effort: ""

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
  show_activity: true
  update_check: true

hooks:
  allow_project_hooks: false

external_projects_enabled: false

parism:
  enabled: true
  fallback_to_bash: true

checkpoint:
  enabled: true
  per_session_max: 50
```
