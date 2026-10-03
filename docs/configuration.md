# Configuration Reference

NerdVana CLI reads configuration from, in order of decreasing priority:

0. Managed policy files an administrator placed in `/etc/nerdvana/managed-settings.d/`
   (and `$NERDVANA_MANAGED_DIR`): they apply above everything below, and the user cannot
   override them. See [managed-policy.md](managed-policy.md).
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
| `NERDVANA_DATA_HOME` | User data root; default `~/.nerdvana`. The MCP server key, ACL and audit files (`mcp_keys.yml`, `mcp_acl.yml`, `audit.sqlite`) live here too; one that exists only in `~/.nerdvana` is still read from there, with a warning |
| `NERDVANA_MANAGED_DIR` | One more directory of managed settings files, read after `/etc/nerdvana/managed-settings.d/`. It must exist when set. Managed files only restrict, so it cannot loosen the system directory. See [managed-policy.md](managed-policy.md) |
| `NERDVANA_AGENTS_COMMAND` | The command `nerdvana agents start` runs for each background run, split like a shell line; default is this interpreter running `nerdvana_cli.main`. See [background.md](background.md) |
| `NERDVANA_SCHEDULE_COMMAND` | The command the scheduler starts to run a job, split like a shell line; default is this interpreter running `nerdvana_cli.main`. See [scheduling.md](scheduling.md) |
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
| `openai_api` | string | `auto` | Which OpenAI API the OpenAI-compatible adapter speaks. `auto`: the Responses API for `provider: openai` on OpenAI's own endpoint (`https://api.openai.com/v1` or an empty `base_url`), Chat Completions for every other server (Groq, Ollama, vLLM, OpenRouter, DeepSeek, MiniMax and the rest). `chat` forces Chat Completions, `responses` forces the Responses API for any OpenAI-compatible endpoint that implements it. The Responses path is stateless: every turn sends the whole conversation with `store: false`, and the encrypted reasoning items the model returns are kept in the session transcript and sent back on later turns. Gemini and Anthropic ignore it. What each adapter supports is listed in [providers-compat.md](providers-compat.md). |
| `gemini_api` | string | `generate_content` | Which Gemini API the `gemini` provider speaks. `generate_content`: the `generateContent` API (the default until the Interactions path is verified against the live API). `interactions`: the Interactions API, stateless: every turn sends the whole conversation as steps with `store: false`, and the thought steps the model returns (with their signatures) are kept in the session transcript and sent back unchanged on later turns. `auto`: picks the Interactions API. Other providers ignore it. What each adapter supports is listed in [providers-compat.md](providers-compat.md). |
| `reasoning_effort` | string | `` | How hard OpenAI-compatible and Gemini models reason, in the provider's own words, sent as it is: OpenAI `reasoning_effort` (`none`, `minimal`, `low`, `medium`, `high`, `xhigh`; which values a model accepts differs, and an unsupported one is refused by the API) and the Gemini thinking level (`minimal`, `low`, `medium`, `high`; any other value stops the request with an error, and which levels a model accepts also differs). Limit: from GPT-5.4 on, OpenAI's Chat Completions endpoint does not support tool calling with a `reasoning_effort` other than `none`, and `nerdvana` always sends tools. `openai_api` (below) sends OpenAI's own endpoint to the Responses API by default, which has no such limit; with `openai_api: chat` and any other value than `none` the request logs a warning once and an API refusal carries the same explanation. On the Responses API a set value also asks for reasoning summaries (unless `show_thinking` is off) and for encrypted reasoning, and the request omits `temperature`. On the Gemini Interactions API (`gemini_api`) a set value is sent as `generation_config.thinking_level` and also asks for thought summaries (unless `show_thinking` is off). Empty leaves the provider's default. Anthropic models ignore it. `nerdvana run --set model.reasoning_effort=high` sets it for one run. |
| `reasoning_effort` | string | `` | How hard models reason, in the provider's own words, sent as it is: Anthropic `output_config.effort` (`low`, `medium`, `high`, `xhigh`, `max`; details at the end of this entry), OpenAI `reasoning_effort` (`none`, `minimal`, `low`, `medium`, `high`, `xhigh`; which values a model accepts differs, and an unsupported one is refused by the API) and the Gemini thinking level (`minimal`, `low`, `medium`, `high`; any other value stops the request with an error, and which levels a model accepts also differs). Limit: from GPT-5.4 on, OpenAI's Chat Completions endpoint does not support tool calling with a `reasoning_effort` other than `none`, and `nerdvana` always sends tools. `openai_api` (below) sends OpenAI's own endpoint to the Responses API by default, which has no such limit; with `openai_api: chat` and any other value than `none` the request logs a warning once and an API refusal carries the same explanation. On the Responses API a set value also asks for reasoning summaries (unless `show_thinking` is off) and for encrypted reasoning, and the request omits `temperature`. Anthropic: the value goes in `output_config.effort` for the models that take effort (Fable 5.x, Mythos 5.x, Opus 4.5 to 5.5, Sonnet 4.6 to 5.5); `xhigh` is limited to the newer families and `max` to all but Opus 4.5, and a value the model does not accept stops the request with an error naming the levels it accepts. A model that takes no effort (Haiku 4.5, and Anthropic-compatible servers with other model names) gets nothing. The thinking mode of a model is not changed by it, so on Opus 4.6 to 4.8 and Sonnet 4.6 thinking still needs `extended_thinking`. At `xhigh` and `max` leave room in `max_tokens`. The level stays the same for the whole conversation, because changing it restarts the prompt cache; models with per-message effort can change it between turns without that (see [providers-compat.md](providers-compat.md)). Empty leaves the provider's default. `nerdvana run --set model.reasoning_effort=high` sets it for one run. |
| `anthropic_tool_search` | | string | `off` | Anthropic only. `bm25` or `regex` turns on Anthropic's server-side tool search: MCP tools are declared with `defer_loading: true` after the matching search tool (`tool_search_tool_bm25_20251119` or `tool_search_tool_regex_20251119`), the model discovers them through the API's own search, and the `server_tool_use` and `tool_search_tool_result` blocks are kept and sent back with the turn. Tools that are not MCP tools stay loaded; nothing is deferred without MCP tools. With it on for a Claude model on the Anthropic provider the loop does not also defer the MCP tools behind the local `ToolSearch` tool (`session.defer_tools` is then read as `never`), so one mechanism does the deferring; with `off`, or on another provider or model, `session.defer_tools` applies as before. `off` declares every tool in full. |
| `anthropic_compaction` | string | `off` | Anthropic only. `on` lets the provider use the API's on-demand compaction (beta header `compact-2026-09-04`) on the models that support it: `AnthropicProvider.compact` returns a signed summary block that is stored with the message, and while it is in the history the provider sends it first and leaves out the messages before it. `core/compact.py` remains the compaction for every other provider and for models without support. The loop asks the provider when the context passes `session.compact_threshold`, between turns: the block is appended to the history as an assistant message and recorded in the session transcript, the request's usage is added to the session's totals and cost, and the context estimate counts only the messages from the last block on. Any failure of the request, or an answer without a summary, falls back to `core/compact.py` for that trigger, and after `session.compact_max_failures` failures in a row the provider is not asked again in the session. A context-limit error in the middle of a request is still handled by `core/compact.py`. Details in [providers-compat.md](providers-compat.md). |
| `anthropic_memory_tool` | bool | `false` | Anthropic only. `true` registers a tool named `memory` that Claude models know as Anthropic's memory tool (`memory_20250818`: view, create, str_replace, insert, delete, rename under `/memories`). The files are kept per project in `<data root>/memory-tool/<project>-<hash>/`, are limited in size (100,000 bytes per file, 5,000,000 bytes and 500 files in all) and refuse paths outside `/memories` and content that looks like a credential. It is not registered for other providers. |

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
| `gate_untrusted_sources` | bool | `true` | Ask before a `Bash` command or a state-changing MCP call (a tool its server does not mark `readOnlyHint`) whose arguments repeat a stretch of at least 31 characters of what `WebFetch`, `WebSearch` or an MCP tool returned earlier in the session, text that was not written by you. The question names the source; counted as the `untrusted_source` signal. Without a way to ask (a run with no terminal) the call is refused. It compares text and does not judge it, so a model that rewrites the instruction in its own words is not caught; it comes on top of the sandbox and the permission rules. `yolo` trust runs without it. |
| `classifier` | string | `"off"` | `off`, `shadow` or `enforce`. A second model call judges each call the policy would run without asking: it sees only what you typed in the session, the tool name with its arguments and the project directory (never what the assistant wrote or a tool returned), answers `allow`, `ask` or `deny` with a reason, and a one-word pre-filter spares the full call when the action is plainly routine. Calls of read-only tools and calls an `always_allow` rule names are not judged. `shadow` changes nothing and records each verdict beside what really happened; `nerdvana approvals` prints the comparison. In shadow mode a call the policy asks about is judged as well, so the verdict can be set against your answer; in enforce mode such a call goes to you as before. `enforce` turns an allow into an ask or a refusal and never relaxes an ask or a refusal. Any failure (no model, a timeout, an answer that cannot be read, the cost limit reached) is an `ask`, so in a run with no terminal an enforced classifier that cannot run refuses the call. Its requests are priced for the model they ran on, count toward `session.max_cost_usd` and appear in `nerdvana cost --by agent` as `classifier`. Counted as the `classifier_ask`, `classifier_deny` and `classifier_error` signals, in both modes. Write `"off"` in quotes: YAML reads a bare `off` as false, which is accepted as off. |
| `classifier_model` | string | `` | Model of the classifier, `model` or `provider:model` (the provider's API key must be in the environment). Empty uses the `classifier` entry of `agents.categories`, then `quick`, then the session model. A model that cannot be used (no key, refused by the managed policy) leaves the classifier without a model, which fails to `ask`. |

Every tool call goes through one policy, in this order: `always_deny`, tools excluded by the active mode, the tool's own refusal, `always_allow`, then the mode's trust level (`strict` asks before any write, `balanced` asks before destructive tools and tools that require confirmation, `yolo` asks nothing). Sub-agents and background agents follow the same policy. With `permissions.classifier` on, a call that would run unasked is judged last.

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
| `no_progress_failed_edits` | int | `3` | Tell the model once when this many edits in a row to one file all failed (a stale or wrong anchor it keeps retrying); counted as `no_progress`. `0` turns the check off. See [goals.md](goals.md). |
| `no_progress_read_turns` | int | `12` | Tell the model once when this many turns in a row used only read-only tools, with no edit and no command run; counted as `no_progress`. `0` turns the check off. |
| `mask_secrets` | bool | `true` | Replace secret-looking values in the output of commands and external tools with `[REDACTED]` before the model sees it. See [secret-masking.md](secret-masking.md). |
| `mask_extra_patterns` | list | `[]` | Regular expressions whose matches are replaced as well. |
| `require_price` | bool | `false` | Refuse to run when `max_cost_usd` is set but the model has no known price, instead of continuing without a cost limit. Also `nerdvana run --require-price`. |
| `max_context_tokens` | int | `180000` | Auto-resolved per model (1M-token models resolve to 1,000,000, so compaction starts near 800,000 tokens); set a smaller value to compact earlier |
| `compact_threshold` | float | `0.8` | Fraction of max_context_tokens that triggers compaction |
| `compact_max_failures` | int | `3` | AI compaction circuit breaker |
| `observation_masking` | bool | `false` | Replace the text of old read-type tool results (FileRead, Glob, Grep, Bash, Parism, web and MCP tools, symbol and LSP lookups) with a short placeholder before compaction runs, so compaction triggers less often. The call and its result stay paired; only the result text changes. Results of edit, write and todo tools, error results, the newest result that shows line anchors for a file, and anything holding an activated skill are never cleared. Counted as `observations_masked` in the `signals` of a run result. |
| `mask_keep_last` | int | `6` | Number of most recent tool results that observation masking always leaves intact |
| `mask_trigger_tokens` | int | `20000` | Estimated tokens of clearable old results that must pile up before observation masking clears them all at once. Clearing in batches keeps the start of the request unchanged between batches, so provider prompt caches stay valid. |
| `report_bash_changes` | bool | `false` | In a git working tree, name the files a `Bash` command changed (new, removed or different content) in a final line of its output: `[Files changed by this command: a.py, b.py]`, ten at most. It compares the dirty files before and after the command, so it costs two `git status` calls per command. |
| `planning_gate` | bool | `false` | Spawn Plan subagent on complex prompts |
| `default_context` | str | `"standalone"` | Default runtime context profile name |
| `default_mode` | str | `"interactive"` | Default runtime mode name (`interactive`, `planning`, etc.) |
| `show_activity` | bool | `true` | Show the live ActivityIndicator widget between the chat log and the input row. Toggled via `/activity on|off`. |
| `stream_idle_timeout` | float | `300.0` | Seconds a provider stream may stay silent before the request is abandoned and treated as a transient failure. `0` disables. |
| `stream_total_timeout` | float | `3600.0` | Seconds one response may take in total. `0` disables. |
| `post_edit_diagnostics` | bool | `true` | After a file or symbol edit, ask the running language server for errors and append only the errors the edit introduced to the tool result. |
| `max_parallel_agents` | int | `5` | Sub-agents (`Agent`, `Swarm`) that may run at once against one provider; the rest wait. |
| `steer_mode` | string | `queue` | What text typed while the agent works does: `queue` holds it for the next step; `interrupt` also stops the step in progress, cancelling the model's response or the running tools (a tool that is still running a second later; its shell commands are ended with SIGTERM, then SIGKILL after five seconds), and starts the next step with the text. `/steer <text>` and Ctrl+T always interrupt. `nerdvana run` and ACP never do. See [background.md](background.md). |
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
| `allow_project_hooks` | bool | `false` | Permit `<cwd>/.nerdvana/hooks/*.py` to run, and project skills (`<cwd>/.agents/skills`, `<cwd>/.nerdvana/skills`, `<cwd>/.claude/skills`) to load. Off by default, and an opt-in alone is not enough: each file must also match an approved SHA-256 digest. See [hooks.md](hooks.md) and [skills.md](skills.md). |

Note: Built-in recovery hooks (`context_limit_recovery`, `json_parse_recovery`, `ralph_loop_check`) are auto-registered in `AgentLoop.__init__` and are not listed here.

### Top-level keys

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `external_projects_enabled` | bool | `false` | Register `ListQueryableProjects`, `RegisterExternalProject`, and `QueryExternalProject`. These hand a registered directory to a read-capable subprocess, so the family stays unregistered until this is set. |
| `model_history` | dict[str, str] | `{}` | Per-provider last-used model, written by `/model` and `/provider`. |

### Managed policy and scheduled runs

Neither has a field in `nerdvana.yml`. The managed policy is read from its own drop-in files
([managed-policy.md](managed-policy.md)); scheduled jobs are stored under the data root in
`schedule/jobs.yml` and managed with `nerdvana schedule` ([scheduling.md](scheduling.md)).
Background runs (`nerdvana agents`, and `Agent` tasks started with `run_in_background`) are
recorded under the data root in `runs/` ([background.md](background.md)).

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
| `include_claude_skills` | bool | `false` | Also load skills from `~/.claude/skills` and `<cwd>/.claude/skills`, one tier below the matching `.agents/skills` and nerdvana skill directories. `.agents/skills` (user and project) is always read. Only each skill's `SKILL.md` is read; bundled files are listed to the model on activation and never run by the loader. Skills under `<cwd>` load only for a trusted project (`hooks.allow_project_hooks` plus `nerdvana skill trust`). See [skills.md](skills.md). |

### `sandbox` (SandboxConfig)

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `mode` | string | `off` | `off`, `auto` (confine where the system supports it) or `require` (refuse a command that cannot be confined). An invalid value stops startup. See [sandbox.md](sandbox.md). |
| `network` | bool or `allowlist` | `true` | `false` also refuses TCP connections and binds from the confined command; needs Linux 6.7 (Landlock ABI 4). `allowlist` lets the command reach only a local egress proxy that passes the hosts in `allowed_domains`; same kernel requirement. A session in `allowlist` mode cannot be widened by an agent definition. See [sandbox.md](sandbox.md). |
| `allowed_domains` | list | `[]` | With `network: allowlist`, the hosts the proxy lets through: a host name, `*.suffix` (any subdomain) or an IP address. An entry that is not one stops startup. An empty list allows nothing. |
| `write_paths` | list | `[]` | Paths a confined command may write in addition to the project directory and the temporary directories. |
| `project_writable` | bool | `true` | The project directory is writable to confined commands. Agent definitions with a `write_scope` turn it off; see [agents.md](agents.md). |
| `scratch_writable` | bool | `true` | `/tmp` and the system temporary directory are writable to confined commands. |
| `edit_scope` | list | unset | Paths (relative to the project) that `FileWrite`, `FileEdit` and the symbol edit tools may change; an edit elsewhere is refused. Unset means anywhere the permissions allow. These tools run in the application, so Landlock cannot confine them. |

### `secrets` (SecretsConfig)

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `proxy_credentials` | map | `{}` | Domain (or `*.suffix`) to the name of an environment variable of the application. With `sandbox.network: allowlist` the egress proxy adds that credential to the plain HTTP requests it forwards to the domain: `VARIABLE` sends `Authorization: Bearer <value>`, `Header-Name:VARIABLE` sends the value as it is in that header. The token never enters the command's environment. The domain must also be in `sandbox.allowed_domains`; a TLS tunnel cannot carry an added header. Cannot be set with `--set`. See [sandbox.md](sandbox.md). |

### MCP servers (`mcp.json`)

Servers are declared in JSON, not in `nerdvana.yml`: `~/.nerdvana/mcp.json` and `<cwd>/.mcp.json`, in a `mcpServers` object keyed by server name (a project entry overrides a global one of the same name). `${VAR}` in `env`, `url`, `headers` and `write_paths` expands from the environment. The connection behaviour, skills over MCP and the answers to `input_required` results are described in [mcp-client.md](mcp-client.md).

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `type` | string | `stdio` | `stdio`, `http` or `sse` (both of the latter use streamable HTTP). |
| `command` | string | | stdio only: the server executable. |
| `args` | list | `[]` | stdio only: its arguments. |
| `env` | dict | `{}` | stdio only: variables added to the environment the server inherits from nerdvana. |
| `url` | string | | http only: the endpoint. |
| `headers` | dict | `{}` | http only: sent with every request, for example `Authorization: Bearer ${KEY}`. |
| `sandbox` | string | `off` | stdio only: `off` starts the server as before, `auto` confines it where the system supports it and logs a warning where it does not, `require` fails the connection when it cannot be confined. Any other value fails the connection naming the server. |
| `write_paths` | list | `[]` | stdio only: paths a confined server may write, besides `/tmp`, `/var/tmp`, the system temporary directory and `/dev`. The project directory is not writable unless listed. `~` expands; a relative path is relative to the directory nerdvana was started in. |
| `network` | bool | `true` | stdio only: `false` also refuses TCP connections and binds from a confined server; needs Linux 6.7 (Landlock ABI 4). Anything but `true` or `false` fails the connection. |

`/mcp` shows how each connected stdio server is confined and `nerdvana doctor` lists it in the `mcp_sandbox` check. Confinement uses the launcher described in [sandbox.md](sandbox.md) and has the same limits: it restricts writing, not reading.

### `goal` (GoalConfig)

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `verify_timeout` | int | `300` | Seconds before a verification command is stopped, together with everything it started. |
| `max_attempts` | int | `5` | Failed verifications before the goal is given up and the run ends as unmet. |
| `output_tail_chars` | int | `4000` | How much of the end of a failing verification output is shown to the model. |
| `auto_verify` | bool | `false` | Without a goal, run the project's detected test command before accepting that a run which changed files is finished: `pytest -q` (a `pyproject.toml`, a `pytest.ini` or Python tests under `tests/`), `npm test` (a `test` script in `package.json`), `cargo test` (`Cargo.toml`) or `go test ./...` (`go.mod`), the first that applies whose program is installed. Nothing detected means no check. It uses `max_attempts`, `verify_timeout` and the sandbox policy like a goal. Sub-agents are not checked. See [goals.md](goals.md). |

See [goals.md](goals.md).

### `memory` (MemoryConfig)

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `review` | bool | `false` | `true`: a memory the agent writes, edits, renames or deletes with the memory tools is not applied. It waits in the project's inbox until the user approves it with `nerdvana memory approve` or `/memory approve`, and neither the prompt hint nor `ReadMemory` and `ListMemories` show it before then. A value that is not a boolean stops startup, so a typo never turns review off. See [memory.md](memory.md). |

### `workflow` (WorkflowConfig)

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `enabled` | bool | `false` | Offer the `Workflow` tool to the model, which runs a declared workflow by name (each call asks for confirmation). `nerdvana workflow run` works whatever this says. A value that is not a boolean stops startup. See [workflows.md](workflows.md). |
| `max_parallel` | int | `4` | Agents of one workflow run working at once; never above `session.max_parallel_agents`. |
| `max_agents` | int | `50` | Most agents one `foreach` fan-out may start. A longer list stops the run before any agent of that step starts. |

### `agents` (AgentsConfig)

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `categories` | map | `{}` | Category name to model for sub-agents, written `model` or `provider:model`. An `Agent` call, a `Swarm` task or an agent definition that names a category runs on the mapped model; see [agents.md](agents.md). |

### `tools` (ToolsConfig)

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `max_result_chars` | map | `{}` | Tool name to the most characters of its result kept in the conversation, overriding the tool's own limit. A key is a tool name (`Bash`, `mcp__github__search_code`) or a glob (`mcp__github__*`); an exact name beats a glob and a longer glob beats a shorter one. A longer result keeps its head and its tail, the full text is saved under `~/.nerdvana/tool-output/<session>/` and the note names the file. The smallest value is 1000. Example: `{Grep: 8000, "mcp__*": 4000}`. |

### `checkpoint` (CheckpointConfig)

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `enabled` | bool | `true` | Automatically save session checkpoints |
| `per_session_max` | int | `50` | Maximum number of checkpoints retained per session |

### `telemetry` (TelemetryConfig)

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `otel` | map | see below | OpenTelemetry traces; the fields are listed under `telemetry.otel`. |

### `telemetry.otel` (OtelConfig)

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `enabled` | bool | `false` | Export OpenTelemetry traces of agent runs, model requests and tool calls. Needs the `otel` extra (`pip install 'nerdvana-cli[otel]'`); without the SDK a notice is printed and tracing stays off. See [observability.md](observability.md). |
| `endpoint` | string | `""` | Base URL of an OTLP/HTTP collector, for example `http://localhost:4318`; `/v1/traces` is appended. Empty uses the `OTEL_EXPORTER_OTLP_ENDPOINT` environment variable (and the other `OTEL_EXPORTER_OTLP_*` variables, such as `OTEL_EXPORTER_OTLP_HEADERS`). |
| `service_name` | string | `nerdvana-cli` | The `service.name` resource attribute. |
| `capture_content` | bool | `false` | Also record the conversation sent with each request, the arguments of each tool call and its result on the spans. Secret values are masked first and each attribute is cut at 32768 characters. Prompts and file contents are still sensitive: send them only to a collector you control. |

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
  openai_api: auto
  gemini_api: generate_content
  anthropic_tool_search: "off"
  anthropic_compaction: "off"
  anthropic_memory_tool: false

permissions:
  mode: default
  always_allow:
    - FileRead
    - Glob
    - Grep
  always_deny: []
  gate_untrusted_sources: true
  classifier: "off"        # off | shadow | enforce
  classifier_model: ""

session:
  persist: true
  max_turns: 200
  max_context_tokens: 180000
  compact_threshold: 0.8
  compact_max_failures: 3
  observation_masking: false # opt-in
  mask_keep_last: 6
  mask_trigger_tokens: 20000
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
