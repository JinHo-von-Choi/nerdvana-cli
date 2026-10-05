# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog 1.1.0](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning 2.0.0](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

- The `minimax` provider's default model is now `MiniMax-M3.1-Flash-Preview` (was `MiniMax-M2`).

### Fixed

- Replaced SHA-1 with SHA-256 for internal file change snapshots in `changed_files.py` to comply with current cryptographic guidelines.
- Hardened `setup.py` against TOCTOU race conditions using EAFP file loading and set the default self-hosted Ollama server URL to `http://localhost:11434/v1`.
- Made directory and file cleanup in `memory` and `skill` commands idempotent with `ignore_errors` and `missing_ok`.
- Added context manager support (`__enter__` and `__exit__`) to `HistoryIndex` for deterministic resource release.

## [1.8.0] - 2026-10-04

### Added

- `session.report_bash_changes` (off by default) names the files a `Bash` command changed in a git working tree at the end of its output.
- The `cache_miss` signal counts requests whose prompt-cache read fell to zero with nothing in the loop to explain it (no compaction, masking, or model switch), and the status bar shows the cache share of the last request's input. `CONTRIBUTING.md` describes the benchmark procedure for a change to the system prompt, the tools or the loop.
- `permissions.gate_untrusted_sources` (on by default): a `Bash` command or a state-changing MCP call whose arguments repeat text returned earlier by `WebFetch`, `WebSearch` or an MCP tool asks first, counted as the `untrusted_source` signal.
- `model.openai_api` (`auto`, `chat`, `responses`): provider `openai` on OpenAI's own endpoint uses the Responses API (stateless, `store: false`, encrypted reasoning items kept in the session); every other OpenAI-compatible endpoint keeps Chat Completions. A warning is logged when tools and a `reasoning_effort` other than `none` go to the Chat Completions endpoint. `docs/providers-compat.md` and adapter contract tests describe and check what each adapter supports.
- Skills follow the Agent Skills standard: `~/.agents/skills` and `<project>/.agents/skills` are scanned, parsing is lenient, the system prompt carries a skill catalog, and the `ActivateSkill` tool loads a skill as `<skill_content name="...">` plus a listing of its bundled files. `nerdvana skill trust <path>` approves a project skill. See `docs/skills.md`.
- Opt-in `session.observation_masking` (`mask_keep_last`, `mask_trigger_tokens`) clears old read-type tool output in batches before compaction, counted as the `observations_masked` signal.
- A note, counted as `no_progress`, after 3 failed edits in a row to one file or 12 read-only turns in a row (`session.no_progress_failed_edits`, `session.no_progress_read_turns`). `goal.auto_verify` runs the detected test command (pytest, npm test, cargo test, go test) before a run that changed files and has no goal is accepted.
- `nerdvana cost` shows the cache hit ratio per row and in total (`Hit %`, `cache_hit_ratio` in `--json`).
- `scripts/bench_agent.py` reports pass^k, records the run environment, and has `--isolate` (one-commit repository per attempt), `--no-network` and an audit of solution-lookup behaviour; `scripts/bench_compare.py` compares two result files with a seeded bootstrap interval.
- `model.reasoning_effort` sets OpenAI `reasoning_effort` and the Gemini thinking level for a session or, with `--set model.reasoning_effort=high`, for one run. The value is sent as written; a Gemini value other than `minimal`, `low`, `medium` or `high` stops the request with an error, and an OpenAI value the model does not accept is refused by the API. Empty (the default) changes nothing.
- `nerdvana acp` runs NerdVana as an Agent Client Protocol (v1) agent over stdio for editors such as Zed (`pip install 'nerdvana-cli[acp]'`): messages, thoughts, tool calls with kinds, locations and diffs, plans, usage and cost, permission questions through `session/request_permission`, `session/cancel`, `session/load`, MCP servers passed by the editor and project slash commands. See `docs/acp.md`.
- Optional OpenTelemetry tracing (`pip install 'nerdvana-cli[otel]'`, `telemetry.otel.*`, user config only): `invoke_agent`, `chat` and `execute_tool` spans with token and cache usage, sub-agent parenting, opt-in secret-masked content capture, and `TRACEPARENT` for `Bash` commands. See `docs/observability.md`.
- The MCP client uses the SDK client: it reaches servers of the 2026-07-28 revision and the 2025 revisions, honours `ttlMs` cache hints, answers `input_required` results through the AskUser channel, offers the skills of servers that declare `io.modelcontextprotocol/skills` (`server:skill`, files checked against the server's SHA-256 manifest), and starts stdio servers confined to a write scope (`sandbox`, `write_paths`, `network` per server). See `docs/mcp-client.md`.
- Anthropic: `model.reasoning_effort` is sent as `output_config.effort` (`AnthropicProvider.set_turn_effort` changes it between turns without restarting the cache on models that support it); `model.anthropic_tool_search` (`off`, `bm25`, `regex`) and `model.anthropic_compaction` (`off`, `on`) use the API's server-side tool search and compaction (the loop falls back to client-side compaction on any error); `model.anthropic_memory_tool` registers the `memory` tool kept in a per-project directory; usage reports `thinking_tokens`.
- `model.gemini_api` (`auto`, `generate_content`, `interactions`; default `generate_content`): a stateless Gemini Interactions API path with thought steps and signatures replayed unchanged.
- `model.effort_planning`, `effort_implementation` and `effort_verification` set the reasoning effort per phase of a run.
- `Advisor` tool and `advisor.*` settings (off by default): a bounded, secret-masked excerpt of the conversation goes to a stronger model at a decision point; `advisor.on_signals` asks it once before a signal-based escalation. See `docs/advisor.md`.
- `permissions.classifier` (`off`, `shadow`, `enforce`) and `permissions.classifier_model`: a two-stage model judge for calls that would run unasked; shadow records verdicts and `nerdvana approvals` compares them with your answers, enforce turns an allow into an ask or deny. Fails to ask. Signals `classifier_ask`, `classifier_deny`, `classifier_error`; cost under agent type `classifier`.
- Hook events `permission_denied` (the handler's message is appended to the refusal as a retry hint), `pre_compact` (can cancel a compaction), `post_compact`, `pre_model_switch`, `post_model_switch` and `instructions_loaded`, bindable in `hooks.yml`. `ConfigChange` is not implemented (see `docs/hooks.md`).
- `tools.max_result_chars` caps the size of a tool's result per tool name or glob, including `mcp__server__*`; the full output is saved to a file.
- `sandbox.network: allowlist` with `sandbox.allowed_domains`: a confined `Bash` command can connect only to a local egress proxy that forwards to the listed domains (exact or `*.suffix`, Landlock ABI 4). `secrets.proxy_credentials` makes the proxy add a token to plain HTTP requests so it never enters the command's environment. Signal `egress_denied`; `nerdvana doctor` reports the network mode.
- Managed settings drop-ins (`/etc/nerdvana/managed-settings.d/*.yml`, `NERDVANA_MANAGED_DIR`) applied above user and project settings: model allow and deny globs, an MCP server allow-list, project hooks forced off, extra `always_deny` rules, a cost ceiling and a sandbox floor; a malformed file refuses to start. `/policy` and a `managed_policy` doctor check. See `docs/managed-policy.md`.
- `nerdvana schedule add|list|remove|run|daemon|install-systemd`: cron and `every 15m` jobs, read-only by default, per-job and daily cost ceilings, overlap locks. See `docs/scheduling.md`.
- Declarative multi-agent workflows (YAML in `.nerdvana/workflows`): dependency-ordered steps, `foreach` fan-out, JSON schema retries, `cross_check` majority voting, a shared cost ceiling and resume from stored results; `nerdvana workflow list|show|run`, a bundled `review-fanout` workflow and a `Workflow` tool (`workflow.enabled`, off by default). See `docs/workflows.md`.
- Background runs: aborting a run cancels the provider request, running `Bash` commands (SIGTERM to the process group, SIGKILL after 5 seconds), MCP calls and web requests at once; background `Agent` tasks and `nerdvana agents start|list|show|attach|stop|resume|clean` keep a durable record with a lease (runs whose process died show as `orphaned`); `nerdvana run --resume SESSION_ID`. See `docs/background.md`.
- `session.steer_mode` (`queue`, `interrupt`), the `/steer` command and Ctrl+T redirect an agent that is working.
- Reviewable memory: entries record created and modified times, source and last read; `memory.review` holds memory-tool changes in an inbox until approved; `nerdvana memory inbox|approve|reject|forget|stale` with an audit log. See `docs/memory.md`.
- `/context` and `nerdvana context [session-id]` show where the context window goes; `/history` and `nerdvana history search` search past session transcripts (FTS5 index, substring fallback, secrets masked). See `docs/context-history.md`.
- `scripts/bench_symbol_tools.py` measures symbol tool success and latency without a model; eight long-horizon benchmark tasks in `benchmarks/long` (tag `long`) with a deterministic generator.
- Token estimates go through one interface that weighs Hangul at 1.5 tokens per character and uses tiktoken for OpenAI-compatible sessions when it is installed.
- `docs/architecture.md` describes the package layout and the allowed dependency direction.

### Changed

- Project skills (`<project>/.agents/skills`, `.nerdvana/skills`, `.claude/skills`) load only when `hooks.allow_project_hooks` is true and the `SKILL.md` digest is approved. Existing project skills stop loading until then.
- MCP tools run next to other calls only when the server marks them `readOnlyHint`, and a tool marked `destructiveHint` asks before it runs; a tool with no annotations is serialized.
- `nerdvana session resume` passes the session id to the TUI as an argument instead of setting `NERDVANA_RESUME`.
- `nerdvana serve` runs on `mcp` 2.x (`pyproject.toml` now asks for `mcp>=2.0.0,<3.0`): the server is an `MCPServer`, the tool list and calls were checked with the mcp 2.x client over stdio and over HTTP with a bearer token. A refused call (access denied, over the quota, malformed) still reaches the client with its reason.
- The provider SDK ranges now allow anthropic 1.x, openai 3.x and google-genai 2.x (the adapters were exercised against anthropic 1.11, openai 3.24 and google-genai 2.28: their test suites pass and a MiniMax tool loop runs through openai 3.24; the Anthropic and Gemini adapters were not called against their real APIs).
- Core is split into subpackages (`config`, `hooks`, `state`, `context`, `safety`, `telemetry`, `execution`, `loop`, `delegation`); LSP and symbol code moved to `codeintel`, external projects to `external`, sub-commands to `cli/commands` and slash handlers to `ui/slash`. The agent loop and the tool executor were decomposed (`agent_loop.py` 836 lines, `tool_executor.py` 463) and everything that builds a loop goes through `cli/bootstrap.py`. Import paths changed, with no compatibility re-exports.
- Korean text now reaches the compaction trigger, the masking trigger, `NIRNA.md` truncation, the tool result caps and the doctor's size check sooner, because Hangul counts 1.5 tokens per character; `compact_messages` sizes messages the same way as the totals.
- A sub-agent's `Agent` and swarm results now report to the parent through `SubagentConfig.absorb`.
- Transcripts start with a `session_start` entry that records the working directory.
- The descriptions of `FileEdit`, `replace_symbol_body`, `Grep`, `Bash`, `Agent`, `Swarm`, `ActivateSkill` and `WebFetch` carry one example call.

### Fixed

- A sub-agent's token totals and signal counts are added to its parent's run result (they were left out; the cost already was), for the `Agent` tool and for swarm workers. A sub-agent that was aborted reported a spend of zero to the cost envelope; it now reports what it spent.
- The MCP server key, ACL and audit files follow `NERDVANA_DATA_HOME`; files that exist only under `~/.nerdvana` are still read from there with one warning. Nothing is copied or deleted.
- A streamed Gemini response that called tools now ends as `tool_use`; it ended as `end_turn` before and the run stopped without executing the calls.
- The git status in the system prompt is taken once per session, so editing files no longer invalidates the provider prompt cache on the next prompt.
- The `total_cost_usd` of a `run` result and the check against `session.max_cost_usd` left out what the session's sub-agents spent, and a session that switched models priced all its tokens at the last model's rates. The total now adds the sub-agents' spend, and each request is priced for the model that served it.
- After an escalation (`session.escalation_model`) the next prompt of the same session went back to the first model, and the escalation could not happen again. The escalated model now stays for the session, and the `run` result names the model that finished the run.
- `nerdvana serve --transport http` answered every MCP request with a 500 ("Task group is not initialized"), because the bearer-auth wrapper did not hand on the lifespan that starts the session manager. A test now completes the handshake through the real app.
- `find_referencing_symbols` and `lsp_find_references` returned only the definition (0 of 33 benchmark runs); they now return references from other files and `lsp_rename` sees them too. The result says when files were left out.
- `replace_symbol_body` kept dropping the blank and comment lines after the symbol and ended at a decorator or the `)` of a multi-line signature; it now keeps them and replaces decorated symbols whole. `safe_delete_symbol` no longer reports every symbol as referenced because of its own definition.
- `nerdvana session list` shows the first user message for current and older transcripts.
- The MCP server's `EditMemory` wrapper takes `needle`, `repl` and `mode`, and `WriteMemory` requires a valid `scope`; neither call could succeed before.

### Removed

- Unused options and definitions of the hook bridge and of the server and tool packages.

## [1.7.0] - 2026-10-03

### Added

- `nerdvana run` reports results as `--output-format text|json|stream-json` and stops at `--max-turns` or `--max-cost-usd`. Exit codes: 0 completed, 1 provider error, 2 missing credentials or invalid usage, 3 limit reached.
- Anthropic requests cache the prompt prefix (`model.prompt_caching`, on by default). Cached reads and writes are counted separately in usage, cost estimates and the analytics store; `session.max_cost_usd` stops a run at a spending limit.
- Text typed while the agent works is delivered at its next step instead of waiting for the turn to end.
- Tool permission prompts open as a modal and show the diff of a file change before it is approved.
- Custom slash commands from `~/.nerdvana/commands/*.md` and `<project>/.nerdvana/commands/*.md` (`$ARGUMENTS`, `$1` to `$9`, optional frontmatter description), and shell command hooks from `hooks.yml` for `before_tool`, `after_tool`, `session_start` and `session_end`. A project `hooks.yml` loads only with `hooks.allow_project_hooks` and an approved digest.
- `sandbox.mode` (`off`, `auto`, `require`) confines what the `Bash` tool can write with the operating system, using Landlock on Linux: a command writes only below the project, the temporary directories and `sandbox.write_paths`, and `sandbox.network: false` refuses TCP connections on Linux 6.7 and later. `nerdvana doctor` reports availability. See `docs/sandbox.md` for what it does not cover.
- `session.max_total_tokens` and `nerdvana run --max-total-tokens` stop a run after a number of input and output tokens without needing a price, and `session.require_price` and `--require-price` refuse a run whose cost limit could not be enforced. `nerdvana doctor` reports models with no known price. `nerdvana run --sandbox off|auto|require` overrides `sandbox.mode` for one run.
- Permission rules can name arguments: `always_allow` and `always_deny` accept `Tool(pattern)` (`Bash(git status)`, `Bash(git diff *)`, `FileWrite(docs/*)`), matched against the command, path or URL of the call; a command with shell operators never matches an allow rule that names arguments. The answers to permission questions are recorded, and `nerdvana approvals` suggests exact rules for calls approved at least three times and never refused. Nothing is written to the configuration.
- `nerdvana serve` exposes `FileRead` (lines tagged with anchors) and `FileEdit` (anchor or exact string, refused unless the calling client read the file and it is unchanged, with new language-server errors reported), so another agent can use NerdVana as its edit backend. Each client has its own read ledger. See `docs/mcp-edit-backend.md`.
- `Agent` with `isolation: worktree` runs the sub-agent in its own git worktree on a new branch: its edits do not touch the project directory, an unchanged worktree is removed afterwards, and a changed one is kept with its branch and path named in the result. See `docs/agents.md`.
- `nerdvana import claude|codex` brings over slash commands (`.claude/commands`, `~/.claude/commands`, `~/.codex/prompts`) into `.nerdvana/commands` without overwriting anything, and prints the `allow` and `deny` rules of a Claude Code `settings.json` converted to this syntax (`Bash(git diff:*)` becomes `Bash(git diff *)`) for you to paste; it shows a plan until `--write`.
- Images in prompts: `nerdvana run --image PATH` and `/image <path> <question>` attach PNG, JPEG, GIF or WebP files (type read from the file's first bytes, up to 5 MB, at most 6). Anthropic, OpenAI-compatible and Gemini requests carry them in their own shapes; the context estimate counts a flat figure per image, and the session transcript keeps the file names, not the pictures.
- `/rewind [N]` goes back before the last N prompts: their messages are dropped and the edits the edit tools made in them are undone through the file checkpoints (what a shell command changed is not). The session transcript records the rewind, so a resumed session agrees. A compaction or a reset ends how far back it can go.
- `/btw <question>` asks a side question with the conversation as context: neither the question nor the answer is added to the history, and the request begins like the agent's own so a cached start is reused.
- `docs/examples/nerdvana-comment.yml` and `docs/github-action.md`: an example GitHub workflow that runs `nerdvana run` when a collaborator comments `/nerdvana <task>`, with a read-only token, cost, turn and sandbox limits, and the decisions it leaves to you.
- A goal can carry a scope (`--scope PATH`): an edit outside it asks first and is refused when nobody can be asked, counted as `out_of_goal_scope`.
- A run result carries a `receipt` when it changed files or had a verification goal: the files the edit tools changed, the verification outcome, the sandbox policy, the cost per agent type (sub-agents included) and counts of the problems met on the way, all measured by the run itself (`receipt_version` 1).
- `nerdvana review --base REF` reviews the working tree against a git ref with the read-only `code-reviewer` agent, starting from the Python functions and classes the change touches and the lines that mention them (`--context-only` prints that without calling a model; `--fail-on` sets the exit code; `--output-format json`). See `docs/review.md`.
- `find_symbol` with `include_body` returns the symbol's source with an anchor `N#hhhhhh` on every line, and a line shown this way can be changed with `FileEdit` (`anchor_hash`) without reading the whole file first, as long as those lines are unchanged. The edit is checked against the shown lines, not the whole file, so a change elsewhere in the file does not stop it; the whole file stays unread, and the shown range follows the edit, so the symbol can be edited again. A symbol that is long is cut at 300 lines and only the shown part counts.
- Secret-looking values in the output of `Bash`, `Parism`, web and MCP tools (credential-named environment values, key and token shapes, JWTs, PEM keys, bearer headers, `password=` assignments) are replaced with `[REDACTED]` before the model sees them, and the result says how many were replaced (`session.mask_secrets`, `session.mask_extra_patterns`). File tools are not masked. See `docs/secret-masking.md`.
- Agent definitions can limit what their sub-agents write (`write_scope`: `none` or a list of paths, and `network`). Commands are confined by the sandbox policy and the file and symbol edit tools are held to `sandbox.edit_scope`; the built-in `Explore`, `Plan` and `code-reviewer` agents write nothing.
- `scripts/bench_recommend.py` compares benchmark runs per task tag (pass rate with an interval, cost per attempt, the runs no other beats) and suggests an `agents.categories` mapping, or says there is not enough data. `nerdvana run --set section.field=value` overrides one setting for a run, and the benchmark harness passes it through (`--set`), which is how thresholds such as `session.compact_threshold` are compared on the same tasks.
- `session.escalation_model` switches to a stronger model, once per session, when the run's signals reach their thresholds (`session.escalation_signals`: a failed verification, a refused repeat, stale-file edits, new language-server errors). Start cheap and escalate only when it is needed.
- MCP tools are deferred when their declarations are large (`session.defer_tools`, `session.defer_tools_threshold`): the system prompt lists them by name, a `ToolSearch` tool loads the ones the model needs, and a loaded tool is declared in full from then on. See `docs/mcp-deferred-tools.md`.
- With `session.max_cost_usd` set, each sub-agent gets a share of the unspent limit (`session.subagent_budget_fraction`, 0.5) instead of running unbounded; it stops at its share and returns a partial result, and its actual spend counts against the session's limit. Parallel agents cannot promise the same money twice. The token total a sub-agent reports now adds up all its requests rather than the last one.
- Goals: `nerdvana run --verify COMMAND` and `/goal <objective> --verify COMMAND` hold the agent to a command. When the model says it is finished the command runs (in the project directory, under the sandbox policy, with a time limit that also ends the processes it started); a failure sends the end of its output back to the model, and the run ends when it passes, when `goal.max_attempts` failures are used up (`error_goal_unmet`, exit code 3) or at another limit. The goal is saved with the session. See `docs/goals.md`; `scripts/bench_agent.py --gate` measures what the check is worth.
- Sub-agents are told at 60% of their turn limit to stop exploring and answer, and the read-only agent types have lower limits (Explore 12, Plan 15, code-reviewer 12) because every request carries the whole conversation.
- The result of a run has a `signals` object that counts what went wrong by kind: stale or unread-file edit refusals, repeated calls, new language-server errors after an edit, invalid input, denied permissions, refusals by the sandbox, todo nudges, provider retries and fallbacks, compactions. The benchmark summary compares them between failed and passed attempts.
- Cost is attributed: each provider request records the agent type, category, parent session, turn and the tool that ran just before it. `nerdvana cost --by agent|category|tool` groups by them (the tool is an order in time, not proof of cause), and `stream-json` carries a `request` event per request with its tokens and cost.
- The system prompt no longer repeats the tool descriptions and parameter schemas that the provider already receives as tool declarations, and lists tool habits only for the tools in use. In an empty directory the fixed input of every request falls from about 14,200 to about 5,800 tokens. `session.project_doc_max_tokens` caps how long a project document (NIRNA.md, AGENTS.md, CLAUDE.md) may be in the prompt, and `nerdvana doctor` reports how much they add. `scripts/measure_prompt_overhead.py` prints the sizes.
- `scripts/bench_agent.py` measures the agent's success rate on fixed repository tasks: each attempt runs `nerdvana run` in a fresh copy of the repository and the task's own verify command decides pass or fail, with pass@k, a bootstrap interval, results by tag, cost and time per task over 20 tasks in `benchmarks/`. It is a manual tool that bills real API usage; see `docs/benchmarks/agent-success-rate.md`.
- Sub-agents can run on a different model: an agent definition takes `model` and `category`, `agents.categories` maps a category to a model (`model` or `provider:model`), and the `Agent` tool and `Swarm` tasks accept `model` and `category`.
- `nerdvana cost` and the session cost read the usage each request reported, with cached read and write tokens as their own columns; sessions recorded before this keep their per-tool-call figures.
- Gemini 3 function calls carry their thought signature back to the API, with the documented stand-in for history that has none, and the results of parallel calls go out in one message.
- Thinking on Anthropic models: Claude 5 models run with adaptive thinking, `model.extended_thinking` and `ultrawork` enable it where it is optional, `model.thinking_budget` applies to manual-budget models, and `model.show_thinking` requests summaries. Thinking blocks are returned unchanged with tool results, which the API requires to keep thinking active across tool turns, and are saved in the session transcript so a resumed conversation keeps them.

### Changed

- The default model is `claude-sonnet-5-5`; `claude-sonnet-4-20250514` is retired. Context windows of 1M tokens are recognised for the models that have them, and `pricing.yml` carries cache rates and the Claude 5 entries.
- `FileRead` reports a binary file by type and size instead of decoding it.
- Unused path helpers, `get_provider_config` and the `ActivateMode` and `DeactivateMode` tools are removed.
- The agent loop and tool executor are split into single-purpose steps, and a contract test keeps function length from growing.

### Fixed

- A turn made of tool calls only (no text) is now saved in the session transcript, so resuming a session no longer drops those calls and their results.
- Escape closes a tool permission prompt instead of being swallowed by the app-wide focus binding.
- OpenAI-compatible streams that report usage on the chunk that carries the finish reason (MiniMax among them) were counted by an estimate that left out the tool declarations and cached tokens. The reported figures are used, once per response, with cached tokens, and the estimate counts the tool declarations. MiniMax-M3 and M2.7 prices carry their cache rates.
- Anthropic tool calls are sent as `tool_use` blocks and their results merged into one user message.
- Duplicate tool call ids no longer reach the API: a stream stops after its tool-use stop, OpenAI-compatible streams emit each call once and keep parallel calls apart, repeated ids are renamed, and ids are repaired before each request.
- Gemini token usage is reported from the stream.
- The symbol edit tools (`replace_symbol_body`, `insert_before_symbol`, `insert_after_symbol`, `safe_delete_symbol`) now get a checkpoint before an applied edit and a check for new language-server errors after it; the executor listed them under names that no tool has.

## [1.6.0] - 2026-10-03

### Added

- Every tool call passes through one permission policy: `permissions.always_deny`, tools excluded by the active mode, the tool's own verdict, `permissions.always_allow`, then the mode's trust level. `--approval-mode`, `session.default_mode` and `permissions.mode` now take effect, and tools declaring `requires_confirmation` ask first.
- Tool arguments are checked against the tool's schema; missing, unknown or mistyped arguments come back to the model as an error instead of being dropped.
- Provider failures are classified (transient, context limit, authentication, decoding). Transient failures are retried with backoff or `Retry-After` (`model.max_retries`), then fall back through `model.fallback_models`, which accepts `provider:model` to switch provider. A context-limit failure compacts the history and retries once.
- `nerdvana session resume <id>` restores the recorded conversation. `session.persist: false` stops transcript writes. Provider streams stop after `session.stream_idle_timeout` seconds of silence or `session.stream_total_timeout` seconds in total.
- Open todo items keep the agent working after it ends a turn, until three reminders in a row make no progress; the list is restated after compaction.
- `AskUser` lets the model ask a clarifying question with suggested answers in the TUI.
- After a file or symbol edit, errors the edit introduced are reported from the running language server (`session.post_edit_diagnostics`).
- Finished background agents are reported to the model at its next step, and an idle TUI session starts a turn to review them.
- Sub-agents run with their definition's system prompt and turn limit, can use the session's LSP, symbol, web and MCP tools (`"@read"` admits read tools), and are limited to `session.max_parallel_agents` per provider. Identical consecutive tool calls draw a warning at the third repeat and are refused at the fifth.
- Root `AGENTS.md` and `CLAUDE.md` are loaded after `NIRNA.md`, and rule files in subdirectories are injected the first time a file there is touched.
- Skills can be directories with a `SKILL.md`; `skills.include_claude_skills` also reads `.claude/skills`.
- Invalid config values fall back to defaults with a warning shown at startup and by `doctor`; permission and credential fields still stop startup. `doctor` checks model resolution, fallback entries and MCP configuration.
- Release workflow builds, smoke-tests and publishes tagged versions to PyPI.

### Changed

- `FileRead` prefixes lines as `N#hhhhhh`. `FileEdit` and `FileWrite` refuse to change an existing file that was not read in the session or changed since it was read; anchors that moved within 20 lines are relocated when unambiguous.
- Tool results are bounded by estimated tokens (30,000; 10,000 for `WebFetch`) instead of characters, keeping head and tail, and the full output is saved under the data home.
- Context use is measured from the provider's reported input tokens plus an estimate of later messages; the estimate counts the system prompt, tool schemas and non-Latin text.
- The language server is sent the new contents of a file that changed since it was opened.
- `uv.lock` is tracked and CI installs from it.

### Fixed

- The Python language server is started as `pyright-langserver --stdio`; it was started as the `pyright` command-line checker, which exits at once, so LSP and symbol tools were unavailable with pyright. Servers that speak stdio by default (`pylsp`, `gopls`, `rust-analyzer`) are no longer passed `--stdio`. `doctor` looks for `pyright-langserver`.
- References for a decorated definition are looked up at the symbol's name instead of its decorator line.
- `/clear` ends the session before emptying the history, so `SESSION_END` hooks see the conversation.
- The `api_keys` section written by `/provider` loads without an unknown-key warning.

### Removed

- `TeamCreate` and `SendMessage`, which had no receiving side.
- `hooks.session_start`, `hooks.before_tool` and `hooks.after_tool`, which were never read; existing keys load with a "no longer used" warning.
- The agent-loop snapshot suite and the `pytest-snapshot` dependency.

## [1.5.0] - 2026-09-11

### Changed

- Pricing rates in `providers/pricing.yml` are declared per 1,000,000 tokens (`input_per_1m` / `output_per_1m`) and `estimate_cost` divides accordingly. Vendors publish per-million rates, so the file now reads directly against their tables. Every provider snapshot was refreshed; entries whose rate could not be read from an official source carry an inline note saying so.
- Anthropic entries include the Claude 5 models. Retired ids across several providers are annotated rather than silently carried.
- Project-local hooks under `<cwd>/.nerdvana/hooks` require both an opt-in (`hooks.allow_project_hooks`, default off) and a recorded SHA-256 approval of the file. Global hooks are unaffected. Approve with `nerdvana_cli.core.user_hooks.trust_project_hook`.
- External project tools register only when `external_projects_enabled` is true. The setting is now a declared field and defaults to false.
- Edit checkpoints copy the files an edit will touch instead of stashing the repository. Unstaged and untracked work stays in the working tree. `undo` and `redo` operate on those copies. Copies are capped at 5 MiB per file and 64 paths per edit, and reclaimed after seven days. Checkpoint entries left by earlier builds are listed as `legacy-stash` and never removed automatically.
- Symbol tools resolve paths against the project root and refuse targets outside it.
- `acl add` and `acl revoke` write `mcp_acl.yml` and exit non-zero when the write fails. A restart is required for a running server to pick the change up.
- `serve` accepts `--tls-key` and passes the TLS material to the HTTP server. Startup is refused when TLS arguments are given but cannot be applied, including under stdio.
- Tool results come back in the order the calls were given, regardless of which ran concurrently.
- `_git_info` caches per working directory for 30 seconds and refreshes off the event loop.
- Symbol edit tools share one base class and run their file access on a worker thread.
- Provider SDK dependencies carry upper bounds.
- CI runs mypy without `--ignore-missing-imports` and on a daily schedule in addition to push and pull request.

### Added

- `nerdvana hook trust <path>`, `nerdvana hook revoke <path>` and `nerdvana hook trusted` manage project-hook approvals from the command line. `trusted` flags entries whose file changed or is gone.
- `scripts/check_docs_consistency.py` and `tests/docs/` verify documented environment variables, tool names and counts, provider counts, paths and subcommands against the code.
- `security` pytest marker for boundary reproduction tests; run them with `pytest -m security`.
- Analytics rows are recorded for tool calls with provider, model and token counts, so `nerdvana cost` and the dashboard read populated data.
- `AFTER_TOOL` and `BEFORE_API_CALL` hooks fire from the tool executor and the agent loop. Messages a `BEFORE_API_CALL` handler injects reach the same request.
- Coverage for `ui/response_runner.py`, `ui/command_dispatcher.py` and `commands/memory_commands.py`.

### Fixed

- MCP stdio responses larger than the previous reader limit no longer leave the connection in a state where later requests wait for the full timeout. A failed `connect` reclaims the subprocess and reader task.
- Multi-line LSP edits keep the text following the end position. File URIs are percent-decoded, and files that cannot be opened are reported rather than skipped silently.
- Concurrent LSP requests are serialised, and responses whose id does not match the caller are held for their own caller instead of being dropped.
- Preview fingerprints and their validation read the same bytes, so unchanged CRLF files and new files no longer report a stale preview.
- `Grep` opens files through the same guarded path helper the file tools use.
- The update notice is printed after subcommand dispatch and goes to stderr under `serve`, leaving stdout to JSON-RPC.
- Naive compaction drops tool results whose originating call is no longer present.
- The `stream_options` retry in the OpenAI provider is limited to the responses that indicate the parameter is unsupported, so authentication and rate limit failures surface once.
- Gemini tool calls carry their identifier through the streaming path, and tool names resolve from a recorded mapping rather than being parsed back out of the identifier.
- Cancelling a generation stops its timer task, clears the streaming and tool status state, and commits the text received so far.
- Context profile names are validated and resolved through `core/paths`, so `NERDVANA_DATA_HOME` is honoured.
- Memory names cannot resolve outside the memory directory.
- File writes replace their target atomically.
- `hook-bridge` denies a prompt submission when the sanitizer rejects it.
- The wrapper written by `install.sh` keeps the `NERDVANA_HOME` chosen at install time.

## [1.4.0] - 2026-07-05

### Changed

- Bash tool subprocesses receive a filtered environment: variables whose names match credential patterns (`API_KEY`, `SECRET`, `PASSW`, `CREDENTIAL`, standalone `TOKEN` segments) are omitted.
- LSP client file reads run in a worker thread instead of the event loop.
- Ruff CI gate covers `tests/` in addition to `nerdvana_cli/`.

### Fixed

- Swarm/team task output and auto-generated plan text no longer include the subagent token count alongside the response text.
- Command modules import UI widgets from `nerdvana_cli.ui.widgets`, restoring a clean strict-mypy run.
- `create_provider` logs a warning when a provider is missing from the class registry before falling back to the OpenAI-compatible implementation.
- Clipboard backend failures are logged at debug level instead of being silently discarded.

## [1.3.0] - 2026-05-19

### Added

- Moonshot AI (Kimi) provider: OpenAI-compatible API via `https://api.moonshot.ai/v1`. Default model: `kimi-k2-instruct`. API key: `MOONSHOT_API_KEY` or `KIMI_API_KEY`.
- Alibaba DashScope (Qwen Cloud) provider: OpenAI-compatible API via `https://dashscope-intl.aliyuncs.com/compatible-mode/v1`. Default model: `qwen3-coder-plus`. API key: `DASHSCOPE_API_KEY` or `ALIBABA_API_KEY`. Supports tools, streaming, vision, thinking. 1M context window.
- MiniMax provider: OpenAI-compatible API via `https://api.minimaxi.chat/v1`. Default model: `MiniMax-M2`. API key: `MINIMAX_API_KEY`. Supports tools, streaming, vision. 1M context window.
- Perplexity provider: OpenAI-compatible API via `https://api.perplexity.ai`. Default model: `sonar-pro`. API key: `PERPLEXITY_API_KEY` or `PPLX_API_KEY`. Web-search-augmented responses; tool calling not supported.
- Fireworks AI provider: OpenAI-compatible API via `https://api.fireworks.ai/inference/v1`. Default model: `accounts/fireworks/models/llama-v3p3-70b-instruct`. API key: `FIREWORKS_API_KEY`.
- Cerebras provider: OpenAI-compatible API via `https://api.cerebras.ai/v1`. Default model: `llama-3.3-70b`. API key: `CEREBRAS_API_KEY`. Catalog: llama-3.3-70b, llama-3.1-8b, llama-4-scout-17b-16e-instruct, qwen-3-32b, qwen-3-235b-a22b-instruct-2507.
- `/update parism` slash sub-command refreshes the bundled `@nerdvana/parism` MCP package to its latest npm release through the npx cache.
- Startup update-check notice: `core/updater.py` adds `cached_or_check` (24 h TTL on-disk cache), `format_update_notice`, and `is_update_check_enabled`. Opt-out via `session.update_check: false` in `nerdvana.yml`, `--no-update-check` CLI flag, or `NERDVANA_NO_UPDATE_CHECK` env var.
- `nerdvana_cli/server/quota.py`: `QuotaPolicy`, `QuotaDecision`, `QuotaStore` (sliding-window in-memory), `QuotaPolicyResolver` (tenant > role > default hierarchy), `QuotaExceeded` exception. Wired into `NerdvanaMcpServer._dispatch` between ACL check and tool execution. `_QuotaErrorMiddleware` returns HTTP 429 with `Retry-After` header (subject to FastMCP version constraints; see `docs/mcp-quota.md`).
- `ACLManager.effective_roles()` public wrapper.
- `ToolResult.tokens` field for downstream token accounting; populated by `tools/agent_tool.py`.
- New CI gates in `.github/workflows/quality-gate.yml`: import-graph zero-new-cycle invariant (baseline in `.import_cycles_baseline.json`) and `pricing.yml` snapshot TTL check (default 90 days, override via `NERDVANA_PRICING_TTL_DAYS`).
- `scripts/bench_lsp.py`: cold-open, diagnostics, goto-definition, and find-references measurement harness.
- `scripts/bench_lsp_diff.py`: markdown delta table generator with warn/fail regression thresholds.
- `scripts/fetch_lsp_bench_fixtures.sh`: pinned-SHA medium-tier fixture downloader.
- `tests/test_bench_lsp_diff.py`: no-regression, warn, and fail scenario coverage for the diff tool.
- `docs/testing-live.md`: per-provider env-var matrix and cost-aware single-provider run recipes.
- `docs/mcp-quota.md`: `mcp_quota.yml` config schema and known FastMCP 1.27 exception-handling limitation.
- `docs/benchmarks/lsp-baseline-2026-05-13.md`: methodology, fixture tiers, and regression policy.

### Changed

- Provider count updated from 15 to 21.
- Parism subprocess spawn now pins `@nerdvana/parism@latest` so the bundled MCP package follows the latest npm release.
- `nerdvana_cli/ui/app.py` reduced from ~1088 to ~615 lines. Widget classes (ActivityIndicator, ChatMessage, CommandMenu, ModelSelector, MultilineAwareInput, ProviderSelector, StatusBar, StreamingOutput, ToolStatusLine) moved to `nerdvana_cli/ui/widgets/`. Streaming response loop extracted to `ui/response_runner.py`. Slash-command routing extracted to `ui/command_dispatcher.py`.
- `nerdvana_cli/core/agent_loop.py`: `_loop` decomposed into `_maybe_compact_messages`, `_handle_max_tokens_stop`, `_handle_end_turn_stop`, and `_handle_tool_use_stop`. Provider classes now imported under `TYPE_CHECKING` only, so the CLI starts without optional provider extras installed.
- `nerdvana_cli/core/subagent.py`: `run_subagent` now returns `(text, total_tokens)` instead of `text` alone.
- `nerdvana_cli/main.py`: `nerdvana hook` and `nerdvana admin` subcommands extracted to `commands/hook_command.py` and `commands/admin_command.py`. Sub-Typer registration consolidated at module top.
- `nerdvana_cli/providers/base.py`: per-provider capabilities, base URLs, default models, and env-var names now loaded from `nerdvana_cli/providers/variants.yml`. Public dict names (`PROVIDER_CAPABILITIES`, `DEFAULT_BASE_URLS`, `DEFAULT_MODELS`, `PROVIDER_KEY_ENVVARS`) preserved for backwards compatibility.
- `nerdvana_cli/server/__init__.py`: `NerdvanaMcpServer` exposed via PEP 562 `__getattr__` so `nerdvana hook` and `nerdvana admin acl` work without the `[mcp]` extras installed.
- `nerdvana_cli/server/mcp_server.py`: `_execute_tool` split into `_call_tool_raw` (returns `ToolResult`) and a string-wrapper `_execute_tool`.
- `nerdvana_cli/server/auth.py`: `authenticate_stdio` resolves the client identity from the running process UID. MCP stdio transport communicates over inherited stdin/stdout pipes, so the running UID is the only meaningful identity; the prior filesystem-path probe is removed. The `socket_path` parameter is retained for signature compatibility and ignored.
- `pyproject.toml` and `NIRNA.md`: provider count updated to 21.

### Removed

- Git-tracked entries for local project instruction and planning files; `.gitignore` now excludes them from future commits.

## [1.2.0] - 2026-04-29

### Added

- **IDE 패널 레이아웃**: Textual 기반 메인 앱에 프로젝트 트리(`project_tree.py`)와 에디터 패널(`editor_pane.py`)을 추가하여 단일 화면에서 파일 탐색·편집·대화가 가능한 IDE형 워크플로우 제공.
- **에디터 IO 분리**: 파일 열기/저장/디버운스 동작을 모듈화하여 멀티 패널 환경에서 안정적으로 동기화.
- **IDE 워크플로우 테스트**: `test_ide_layout`, `test_ide_workflow`, `test_editor_io`, `test_editor_pane`, `test_project_tree` 등 패널 동작 회귀 테스트 신규 추가.

### Changed

- **심볼 도구 모듈 분리**: `tools/symbol_tools.py`(897줄)에서 편집 책임을 `tools/symbol_edit_tools.py`(722줄)로 분리하여 단일 책임 원칙 준수 및 가독성 향상.
- **LSP 도구 확장**: `tools/lsp_tools.py`에 IDE 패널 연동에 필요한 신규 헬퍼 추가.
- **세션 코어 정리**: `core/session.py` 인터페이스를 멀티 패널 컨텍스트에 맞춰 미세 조정.

## [1.1.1] - 2026-04-24

### Added

- **Featherless AI provider**: OpenAI-compatible API via `https://api.featherless.ai/v1`. Default model: `featherless-llama-3-70b`. API key: `FEATHERLESS_API_KEY`. Note: standard endpoints do not support streaming.
- **Xiaomi MiMo provider**: OpenAI-compatible API via `https://token-plan-sgp.xiaomimimo.com/v1`. Default model: `mimo-v2.5-pro`. API key: `MIMO_API_KEY` or `XIAOMI_API_KEY`. Supports tools, streaming, vision, thinking. 1M context window.
- **Ollama self-hosted mode**: Setup wizard now supports three deployment modes: Local (default), Cloud (`https://ollama.com/v1`), Self-hosted (custom URL).
- **Context block splitting**: `split_into_blocks()` for topic-based conversation segmentation.
- **Block summarization**: `summarize_block()` and `compact_with_blocks()` for Memento-style context compression.
- **Memory importance tracking**: `MemoryEntry.importance` field (0.0-1.0), `min_importance` filter in `list_memories()`, `list_stale()` for time-based cleanup.
- **Agent context sharing**: `create_shared_context()` for summarized context sharing between agents.
- **Session restoration optimization**: `save_summary()`, `get_summary()`, `restore_with_summary()` for fast session recovery.

### Changed

- Provider count updated from 13 to 15.
- Ollama setup wizard enhanced with self-hosted URL input option.
- `nerdvana.yml.example` updated with new provider documentation.

## [1.1.0] - 2026-04-23

### Added

- Featherless AI and Xiaomi MiMo providers (same as 1.1.1 above).

## [1.0.0] - 2026-04-18

First stable release. The 0.9.x series shipped the full roadmap surface
area but a post-release audit uncovered five runtime-breaking defects in
the freshly landed server layer — the MCP server could not even boot,
authentication was defined but not wired, and the external-project tools were not
registered. 1.0.0 closes all five Critical issues, five Major drift
items, and two test-isolation bugs that were hiding real regressions
behind developer-machine pollution.

### Fixed

- **C-1** `nerdvana serve` boots. `rich.console.Console.print` does not
  accept a `file=` keyword; replaced with a dedicated
  `Console(stderr=True)` instance. Added a `serve` start-up regression
  test so the whole G1 → H server stack never silently breaks again.
- **C-2** `NerdvanaMcpServer._execute_tool` is now a real dispatcher:
  it builds a per-server tool map at init time, runs the BaseTool
  `parse_args → validate_input → call` chain, and serialises errors as
  `{"error": "..."}`. Previously the MCP server accepted tool calls
  but returned empty responses.
- **C-3** Authentication and ACL enforcement are wired into the request
  path. `_BearerAuthMiddleware` validates `Authorization: Bearer` for
  every HTTP request and parks the `AuthResult` in a `ContextVar`;
  stdio dispatch consults the uid check via `_verify_stdio_auth`;
  mTLS no longer fails open for unknown CNs. `client_identity` is
  now resolved per request instead of every caller being `anonymous`.
- **C-4** `auth.py` uses `hmac.compare_digest` for hash comparisons,
  closing a timing side-channel.
- **C-5** The external-project tools override the correct `check_permissions`
  signature (was `check_permission`, singular + wrong parameters) and
  return proper `PermissionResult(behavior=PermissionBehavior.*)`
  values. `ListQueryableProjects` is `ALLOW`; `RegisterExternalProject`
  and `QueryExternalProject` are `ASK` (filesystem write / subprocess
  spawn). Dead stubs deleted.
- **M-1** `create_tool_registry` registers the three external-project
  tools (`ListQueryableProjects`, `RegisterExternalProject`,
  `QueryExternalProject`). Gated by
  `settings.external_projects_enabled` so operators can disable the
  subprocess surface.
- **M-3** `BashTool` blacklist covers `$(…)`, `${…}`, and backtick
  substitutions, plus `eval`/`exec`, env-prefixed `sudo`, and
  `dd of=/dev/<block>`. A `_MAX_TIMEOUT = 600` ceiling is now enforced
  in `check_permissions`.
- **M-4** ASK permission has a concrete UX. TTY sessions prompt
  `Allow? [y/N]`; pipes / CI / `EOFError` default to `DENY`.
  Decisions are logged at `INFO`.
- **M-7** Removed the dead `state: LoopState` parameter that
  `ToolExecutor.run_batch` carried over from an earlier split.
- **M-8** `audit.sqlite` is created atomically with
  `os.open(O_CREAT | O_EXCL, 0o600)`, eliminating the
  connect → chmod race. Both `AuditLogger` and `SanitizerAudit` go
  through the shared helper.
- **Test isolation** `tests/test_memories.py` and
  `tests/test_memory_tools.py` now monkeypatch
  `core_paths.global_memories_dir` to a tmp path so the developer's
  real `~/.nerdvana/memories/global/` contents never leak into the
  test run. Four pre-existing false failures disappear.

### Changed

- **M-5** `/help` output is generated from `ui.app.SLASH_COMMANDS`
  and the README REPL Slash Commands table is regenerated from the
  same list (21 entries). Drift prevention script checks it on every
  pre-commit.
- **M-6** Provider count confirmed at 13 (`ProviderName` enum
  includes ZAI/ZhipuAI). Lingering "12 platforms" copy fixed in
  `main.py` help text and README prose.
- **N-2** Production ruff violations cleared (F401 ×2, SIM105, UP017
  → 0). `contextlib.suppress(Exception)` replaces silent
  `try/except/pass`; `datetime.UTC` replaces `timezone.utc` in the
  `tool_executor` path.

### Added

- 130+ new tests across `tests/server/`, `tests/test_bash_*`,
  `tests/test_ask_permission_ux.py`,
  `tests/test_external_tools_*`,
  bringing the full suite to 1003 passed in `-m "not lsp_integration"`.

[Unreleased]: https://github.com/JinHo-von-Choi/nerdvana-cli/compare/v1.8.0...HEAD
