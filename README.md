<p align="center">
  <img src="docs/logo.png" alt="NerdVana CLI" width="480">
</p>

<p align="center">
  <strong>AI-powered CLI development tool — 21 AI platforms, one interface</strong>
</p>

<p align="center">
  <a href="#installation"><img src="https://img.shields.io/badge/install-one--line-blue?style=flat-square" alt="Install"></a>
  <img src="https://img.shields.io/badge/python-%3E%3D3.11-3776AB?style=flat-square&logo=python&logoColor=white" alt="Python">
  <img src="https://img.shields.io/badge/providers-21-green?style=flat-square" alt="Providers">
  <img src="https://img.shields.io/badge/license-MIT-yellow?style=flat-square" alt="License">
  <img src="https://img.shields.io/badge/version-1.7.0-orange?style=flat-square" alt="Version">
  <a href="https://github.com/JinHo-von-Choi/nerdvana-cli"><img src="https://img.shields.io/github/stars/JinHo-von-Choi/nerdvana-cli?style=flat-square&color=brightgreen" alt="Stars"></a>
  <img src="https://img.shields.io/badge/LSP--CI-passing-green?style=flat-square" alt="LSP CI">
</p>

<p align="center">
  Anthropic Claude &middot; OpenAI &middot; Google Gemini &middot; Groq &middot; DeepSeek &middot; Mistral &middot; Ollama &middot; and more
</p>

---

## Features

- **Multi-Provider Support** — works with 21 AI platforms from one CLI
- **Interactive REPL** — conversational coding with streaming output and a live TaskPanel for background agents
- **Non-interactive mode** — single prompt execution for scripting
- **Startup update notice** — on every invocation, a dim one-line notice is printed when a newer GitHub release is available (cached 24 h). Disable via `--no-update-check`, `NERDVANA_NO_UPDATE_CHECK=1`, or `session.update_check: false` in `nerdvana.yml`.
- **Edit integrity**: `FileRead` tags every line `N#hhhhhh`; `FileEdit` and `FileWrite` refuse to change a file that was not read in the session or changed since, and relocate anchors moved by the agent's own edits. `find_symbol` with `include_body` shows a symbol with anchors, and those lines can be edited without reading the whole file. After an edit, errors it introduced are reported from the running language server.
- **Sub-agents and background work**: `Agent` and `Swarm` run sub-agents with their own system prompt, turn limit and tool scope, at most `session.max_parallel_agents` per provider. Finished background agents are reported to the model automatically, and an idle session wakes up to review them.
- **Recovery and completion**: provider failures are classified and retried with backoff, then fall back across models or providers; a too-long context is compacted and retried; stalled streams time out. Open todo items keep the agent working until it stops making progress. See [Self-Recovery](#self-recovery).
- **Permission policy**: `--approval-mode`, `permissions.mode`, `always_allow` and `always_deny` apply to every tool call, including sub-agents. See [Permissions and Approval Modes](#permissions-and-approval-modes).
- **Clarifying questions**: the `AskUser` tool lets the model ask instead of guessing. Text you type while the agent is working is applied at its next step, and a confirmation for a file change shows the diff.
- **Your own commands and hooks**: markdown command templates and shell command hooks. See [Custom Commands and Command Hooks](#custom-commands-and-command-hooks).
- **Claude Code compatible instructions**: root `AGENTS.md` and `CLAUDE.md` load after `NIRNA.md`; rule files in subdirectories are injected when a file there is first touched. Skills follow the Agent Skills standard: `SKILL.md` directories from `.agents/skills`, `.nerdvana/skills` and `.claude/skills`, listed to the model as a catalog and loaded on demand (see [docs/skills.md](docs/skills.md)).
- **Live activity indicator + think-tag rendering** — `<think>...</think>` blocks from DeepSeek-R1, QwQ, Qwen3-thinking, GLM, Kimi K2.5 thinking, MiniMax M2 are split into a dim italic block; an `ActivityIndicator` widget shows the current phase (idle / thinking / waiting_api / streaming / tool_running) and active tool target.
- **Tool System** — Bash, FileRead, FileWrite, FileEdit, Glob, Grep, Parism, Agent, Swarm, TaskGet, TaskStop, plus four LSP tools
- **GitHub comments**: an example workflow runs `nerdvana run` when a collaborator comments `/nerdvana <task>`, with cost, turn and sandbox limits and a read-only token; see [docs/github-action.md](docs/github-action.md)
- **MCP Integration** — connect external MCP servers for additional tools (`mcp__{server}__{tool}`); when their declarations get large they are listed by name and loaded on demand, see [`docs/mcp-deferred-tools.md`](docs/mcp-deferred-tools.md)
- **Edit backend for other agents** — `nerdvana serve` also exposes `FileRead` (anchored lines) and `FileEdit` (refused unless this client read the file and it is unchanged), with a read ledger per client. See [`docs/mcp-edit-backend.md`](docs/mcp-edit-backend.md).
- **MCP server per-tenant quota** — `nerdvana serve` supports rpm / rph / daily_tokens / max_concurrent limits per client, configured via `mcp_quota.yml`. See [`docs/mcp-quota.md`](docs/mcp-quota.md).
- **Session Persistence**: JSONL transcripts; `nerdvana session resume <id>` restores the conversation
- **Auto Provider Detection** — picks the right provider from model name
- **Configurable** — YAML config, environment variables, CLI flags, fallback models, and complexity-triggered planning gate

## Supported Providers

| Provider | Default Model | API Key Env Var |
|----------|--------------|-----------------|
| **Anthropic** | claude-sonnet-5-5 | `ANTHROPIC_API_KEY` |
| **OpenAI** | gpt-4.1 | `OPENAI_API_KEY` |
| **Google Gemini** | gemini-2.5-flash | `GEMINI_API_KEY` |
| **Groq** | llama-3.3-70b-versatile | `GROQ_API_KEY` |
| **OpenRouter** | anthropic/claude-sonnet-4 | `OPENROUTER_API_KEY` |
| **xAI (Grok)** | grok-3 | `XAI_API_KEY` |
| **Ollama** | qwen3 | `OLLAMA_API_KEY` |
| **vLLM** | Qwen/Qwen3-32B | `VLLM_API_KEY` |
| **DeepSeek** | deepseek-chat | `DEEPSEEK_API_KEY` |
| **Mistral** | mistral-medium-latest | `MISTRAL_API_KEY` |
| **Cohere** | command-r-plus | `CO_API_KEY` |
| **Together AI** | Llama-3.3-70B-Instruct-Turbo | `TOGETHER_API_KEY` |
| **ZAI (GLM)** | glm-4.7 | `ZHIPUAI_API_KEY` |
| **Featherless AI** | featherless-llama-3-70b | `FEATHERLESS_API_KEY` |
| **Xiaomi MiMo** | mimo-v2.5-pro | `MIMO_API_KEY` |
| **Moonshot AI (Kimi)** | kimi-k2-instruct | `MOONSHOT_API_KEY` |
| **Alibaba DashScope (Qwen)** | qwen3-coder-plus | `DASHSCOPE_API_KEY` |
| **MiniMax** | MiniMax-M2 | `MINIMAX_API_KEY` |
| **Perplexity** | sonar-pro | `PERPLEXITY_API_KEY` |
| **Fireworks AI** | accounts/fireworks/models/llama-v3p3-70b-instruct | `FIREWORKS_API_KEY` |
| **Cerebras** | llama-3.3-70b | `CEREBRAS_API_KEY` |

## Installation

```bash
curl -fsSL https://raw.githubusercontent.com/JinHo-von-Choi/nerdvana-cli/main/install.sh | bash
```

This installs NerdVana CLI to `~/.nerdvana-cli/` with a virtual environment and adds `nerdvana` / `nc` commands to your PATH.

Requirements: Python >= 3.11, git

### Install from PyPI

Available once the package is published to PyPI. Until then, use the one-line installer above.

```bash
uv tool install nerdvana-cli
# or
pipx install nerdvana-cli

# With every provider SDK
uv tool install "nerdvana-cli[all]"
```

Roll back to an earlier release by pinning it:

```bash
uv tool install nerdvana-cli==<previous-version>
# or
pipx install --force nerdvana-cli==<previous-version>
```

### Manual install

```bash
git clone https://github.com/JinHo-von-Choi/nerdvana-cli.git
cd nerdvana-cli
pip install -e ".[all]"

# Or install with specific providers only
pip install -e ".[anthropic]"   # Anthropic only
pip install -e ".[openai]"      # OpenAI only
pip install -e ".[gemini]"      # Gemini only
```

## Quick Start

```bash
# Set API key for your provider
export ANTHROPIC_API_KEY="sk-ant-..."
# or
export OPENAI_API_KEY="sk-..."
# or
export GEMINI_API_KEY="..."

# Interactive REPL (auto-detects provider from model)
nerdvana

# Specify provider explicitly
nerdvana --provider anthropic --model claude-opus-5-5
nerdvana --provider openai --model gpt-4.1
nerdvana --provider gemini --model gemini-2.5-pro
nerdvana --provider groq --model llama-3.3-70b-versatile
nerdvana --provider ollama --model qwen3

# Single prompt
nerdvana run "explain the architecture of this project"
nerdvana run "refactor this code" --provider deepseek

# List all providers
nerdvana providers
```

## Custom Commands and Command Hooks

**Commands.** A markdown file under `~/.nerdvana/commands/` or `<project>/.nerdvana/commands/` becomes a slash command: `review.md` is `/review`, and `git/commit.md` is `/git:commit`. Typing it sends the file's text as the prompt, with `$ARGUMENTS` replaced by what you typed after the command and `$1` to `$9` by its words (quote a word that contains spaces). Without a placeholder the arguments are appended to the text. An optional `description:` in YAML frontmatter shows in the command menu. A project file replaces a global one of the same name; built-in commands and skills always win.

**Command hooks.** `~/.nerdvana/hooks.yml`, and `<project>/.nerdvana/hooks.yml`, run shell commands on agent events:

```yaml
hooks:
  - event: before_tool        # before_tool, after_tool, session_start or session_end
    match: "Bash"             # tool name glob (before_tool and after_tool), default "*"
    command: "scripts/check-command.sh"
    timeout: 5                # seconds, 1 to 30
```

The command runs in the project directory and receives one JSON object on stdin (`event`, `tool_name`, `tool_input`, `cwd`, and `tool_result` for `after_tool`); `NERDVANA_HOOK_EVENT` and `NERDVANA_TOOL_NAME` are set. Exit code `0` carries on. Exit code `2` blocks a `before_tool` call and tells the model the command's output, or passes an `after_tool` command's output to the model as a message. Any other exit code, a timeout, or a command that cannot start is logged and ignored, so a broken hook never stops the agent. The agent waits for a hook while it runs, so keep them fast.

A project `hooks.yml` runs shell commands that come with the repository, so it follows the rules of project Python hooks: it needs `hooks.allow_project_hooks: true` and an approved digest (`nerdvana hook trust <path>`), and editing the file revokes the approval.

## Headless Runs

`nerdvana run` runs one prompt and exits, which is what scripts, CI jobs and programs that embed the agent need.

```bash
nerdvana run "fix the failing test" --approval-mode yolo --max-turns 30 --max-cost-usd 2 --output-format json
```

| Option | Meaning |
|-|-|
| `--output-format text\|json\|stream-json` | `text` (default) streams for a person to read. `json` prints one result object at the end. `stream-json` prints one JSON event per line and ends with the same result object. In both JSON formats stdout carries only JSON; notices go to stderr. |
| `--max-turns N` | Stop after N model turns. |
| `--max-cost-usd X` | Stop once the estimated cost of the run reaches X USD (needs a known price for the model). |
| `--max-total-tokens N` | Stop once the input and output tokens of all requests reach N. Needs no price, so it works for any model. |
| `--image PATH` | Attach an image (PNG, JPEG, GIF or WebP, up to 5 MB, at most 6) to the prompt (repeatable). The type is read from the file's first bytes; a model that cannot take images answers with its provider's error. |
| `--set section.field=value` | Override one setting for this run (repeatable; the value is read as YAML), e.g. `--set session.compact_threshold=0.5`. The `permissions`, `hooks` and `sandbox` sections cannot be changed this way; use their own options. |
| `--scope PATH` | With `--verify`: paths the task is about (repeatable). An edit outside them asks first, and is refused when nobody can be asked. |
| `--verify COMMAND` | The command that decides whether the task is done. When the model says it is finished the command runs, and if it does not exit with status 0 the end of its output goes back to the model, which keeps working. The run ends when it passes, after `--verify-attempts N` failures (default `goal.max_attempts`, 5) or at a turn or cost limit. The result gets a `verification` object. |
| `--sandbox off\|auto\|require` | Confine what shell commands can write for this run, overriding `sandbox.mode` (see [docs/sandbox.md](docs/sandbox.md)). |
| `--require-price` | Refuse to run when `--max-cost-usd` is set but the model has no known price. |
| `--approval-mode default\|auto_edit\|yolo\|plan` | Permission preset. Without a terminal a confirmation is refused, so unattended runs that write files usually need `yolo`. |

Exit codes: `0` success, `1` the run failed (provider error, unexpected error), `2` invalid options or configuration (a missing API key included), `3` a turn, cost, token or verification limit stopped the run.

The result object (`schema_version` 1; fields are only ever added):

```json
{"type": "result", "schema_version": 1, "subtype": "success", "is_error": false,
 "result": "final answer text", "session_id": "ab12cd34", "provider": "anthropic",
 "model": "claude-sonnet-5-5", "num_turns": 4, "duration_ms": 18234, "total_cost_usd": 0.0421,
 "usage": {"input_tokens": 51230, "output_tokens": 2210, "cache_read_tokens": 38000, "cache_write_tokens": 9000},
 "signals": {"cas_rejected": 1, "new_diagnostics": 2}}
```

`receipt` (present when the run changed files or had a `--verify` goal) puts the evidence beside the claim: `files_changed` (applied edits per file), `verification`, the `sandbox` policy, `cost_by_agent` (requests and USD per agent type, sub-agents included) and `problems` (counts of stale-file edit refusals, new language-server errors, refused repeats, out-of-scope edits, masked secrets, failed verifications, escalations). It is assembled from what the run measured; the model writes none of it. `receipt_version` is 1. `signals` counts what went wrong during the run by kind (`cas_rejected`, `repeat_refused`, `new_diagnostics`, `invalid_input`, `permission_denied_user`, `sandbox_denied`, `tool_error`, `todo_nudge`, `provider_retry`, `provider_fallback`, `compaction`, `observations_masked`, `no_progress`, `untrusted_source` and a few more); a kind that did not occur is absent. `subtype` is `success`, `error_max_turns`, `error_max_cost`, `error_max_total_tokens`, `error_goal_unmet`, `error_unpriced`, `error_max_tokens`, `error_provider`, `error_during_run` or `error_config`; an error result also has an `error` string when one is known. `result` is the text the model wrote after its last tool call.

`stream-json` events, one per line, before the result: `system` (subtype `init`, with the session id, provider and model), `text` (a piece of the answer), `notice` (a message from the agent itself, such as a retry or a fallback), `tool_start` (`name`, `summary`), `tool_done` (`name`, `is_error`), `request` (one provider request: `provider`, `model`, `agent_type`, `turn`, `last_tool`, the token counts including `cache_read_tokens` and `cache_write_tokens`, and `cost_usd`), `compaction` and `context` (percent of the window used).

## CLI Subcommands

### Main commands

| Subcommand | Purpose |
|-|-|
| `nerdvana` | Start interactive REPL (default when no subcommand given) |
| `nerdvana run <prompt>` | Run a single prompt non-interactively |
| `nerdvana setup` | Interactive setup wizard — choose provider, enter API key, select model |
| `nerdvana providers` | List all supported AI providers |
| `nerdvana version` | Show version |
| `nerdvana serve` | Start NerdVana as an MCP 1.0 server (stdio or HTTP transport) |
| `nerdvana doctor` | Diagnose installation, keys, and external dependencies (`--strict`, `--json`) |
| `nerdvana import claude\|codex` | Bring over slash commands (`.claude/commands`, `~/.codex/prompts`) into `.nerdvana/commands`, never overwriting, and print the permission rules of a Claude Code `settings.json` converted to this syntax; shows a plan until `--write` |
| `nerdvana review` | Review the working tree against a git ref with a read-only agent that starts from the changed functions and the lines that use them (`--base`, `--context-only`, `--fail-on`); see [docs/review.md](docs/review.md) |
| `nerdvana approvals` | Suggest `always_allow` rules (`Bash(git status)`) for permission questions you keep approving; nothing is written |
| `nerdvana cost` | Aggregate token usage, cached tokens, the cache hit ratio (cache reads over input, `Hit %` and `cache_hit_ratio`) and USD cost over a time window, from the usage each request reported. `--by provider\|model\|agent\|category\|tool` says where the money went |

### Session transcripts (`nerdvana session ...`)

| Subcommand | Purpose |
|-|-|
| `nerdvana session list` | List stored JSONL transcripts under `~/.nerdvana/sessions/` |
| `nerdvana session resume <id>` | Reopen the REPL on an existing transcript |
| `nerdvana session purge` | Delete stored transcripts |

### MCP servers (`nerdvana mcp ...`)

| Subcommand | Purpose |
|-|-|
| `nerdvana mcp list` | List servers declared in `~/.nerdvana/mcp.json` and `<cwd>/.mcp.json` |
| `nerdvana mcp add <name>` | Add a server entry to one of those files |
| `nerdvana mcp remove <name>` | Remove a server entry |

### Skills (`nerdvana skill ...`)

| Subcommand | Purpose |
|-|-|
| `nerdvana skill list` | List built-in, global, and project skills |
| `nerdvana skill show <name>` | Print one skill's frontmatter and body |
| `nerdvana skill install <source>` | Install a skill into `~/.nerdvana/skills/` |
| `nerdvana skill remove <name>` | Delete an installed skill |
| `nerdvana skill trust <path>` | Approve a project skill so it may load (see [docs/skills.md](docs/skills.md)) |

### Project memories (`nerdvana memory ...`)

| Subcommand | Purpose |
|-|-|
| `nerdvana memory list` | List memories recorded for the current project |
| `nerdvana memory add <text>` | Record a new memory |
| `nerdvana memory remove <id>` | Delete one memory |
| `nerdvana memory purge` | Delete every memory in the selected scope |

### Hook bridge (`nerdvana hook ...`)

| Subcommand | Purpose |
|-|-|
| `nerdvana hook pre-tool-use` | Handle a pre-tool-use hook event — reads JSON from stdin, writes response to stdout |
| `nerdvana hook post-tool-use` | Handle a post-tool-use hook event |
| `nerdvana hook prompt-submit` | Handle a prompt-submit hook event |
| `nerdvana hook list` | List all supported hook event types |
| `nerdvana hook trust <path>` | Approve a project-local hook so it may run |
| `nerdvana hook revoke <path>` | Drop the approval recorded for a project-local hook |
| `nerdvana hook trusted` | List approved project hooks and flag ones whose contents changed |

`nerdvana hook list` works without the `[mcp]` extras installed — the server package is loaded lazily. See [`docs/hooks.md`](docs/hooks.md) for the full event reference.

### ACL management (`nerdvana admin acl ...`)

| Subcommand | Purpose |
|-|-|
| `nerdvana admin acl list` | List all clients and their assigned roles |
| `nerdvana admin acl add <client> <roles>` | Add or update a client's role assignments |
| `nerdvana admin acl revoke <prefix>` | Revoke ACL entries for clients whose name matches a prefix |

`nerdvana admin acl` commands work without the `[mcp]` extras installed.

## Directory Layout

NerdVana CLI separates *install* from *user data*:

```
~/.nerdvana-cli/     — Install root (git repo + venv). Managed by install.sh.
                       Read-only at runtime — never edit this directory by hand.

~/.nerdvana/         — User data root ($NERDVANA_DATA_HOME overrides).
  ├── config.yml     — Global settings
  ├── NIRNA.md       — Global instructions
  ├── mcp.json       — Global MCP servers
  ├── sessions/      — Conversation transcripts
  ├── skills/        — Global user skills
  ├── hooks/         — Global user hooks
  ├── agents/        — Reserved for global agent definitions (not loaded yet)
  ├── teams/         — Team state
  ├── cache/         — Runtime caches
  └── logs/          — Logs (reserved)

<project>/           — Your working directory (optional per-project overrides)
  ├── nerdvana.yml
  ├── NIRNA.md
  ├── .mcp.json
  └── .nerdvana/
      ├── skills/
      ├── hooks/
      └── agents/
```

### Environment variables

| Variable | Purpose | Default |
|---|---|---|
| `NERDVANA_HOME` | Install root | `~/.nerdvana-cli` |
| `NERDVANA_DATA_HOME` | User data root | `~/.nerdvana` |
| `NERDVANA_CONFIG` | Explicit config file path | `~/.nerdvana/config.yml` |
| `NERDVANA_NO_UPDATE_CHECK` | Set to `1` to skip the startup version check | unset |
| `NERDVANA_EXTERNAL_PROJECTS_ROOT` | Boundary root the external project tools may not escape | unset |
| `NERDVANA_EXTERNAL_PROJECTS_ENABLED` | Register the external project tools without editing the config file | `false` |

### Migration

On first run after upgrading, the CLI moves any data from `~/.nerdvana-cli/sessions/` and `~/.config/nerdvana-cli/` into `~/.nerdvana/`. A `.migrated` sentinel prevents reruns.

## REPL Slash Commands

| Command | Description |
|---------|-------------|
| `/help` | Show help |
| `/clear` | Clear chat |
| `/init` | Generate NIRNA.md (alias: `/setup`) |
| `/model` | Show/change current model (per-provider history persists across restarts) |
| `/models` | List available models for the current provider; cursor starts on the active model |
| `/provider` | Add/switch provider (selection persists across restarts) |
| `/mode` | Activate/deactivate mode profile |
| `/context` | Set context profile |
| `/mcp` | MCP server status |
| `/tokens` | Show token usage |
| `/skills` | List available skills |
| `/tools` | List tools |
| `/update` | Check and install updates (`/update parism` refreshes the bundled Parism MCP package to its latest version) |
| `/memories` | List project memories |
| `/undo` | Restore pre-edit git checkpoint |
| `/rewind` | `/rewind [N]` goes back before the last N prompts: their messages are dropped and the edits the edit tools made in them are undone (edits made by shell commands are not) |
| `/redo` | Re-apply last undone checkpoint |
| `/checkpoints` | List session checkpoints |
| `/route-knowledge` | Classify content → suggest WriteMemory scope |
| `/dashboard` | Toggle observability dashboard |
| `/health` | Show 7-day tool call health summary |
| `/image` | `/image <path> [<path> ...] <question>` sends a prompt with the image files at the start attached; the transcript keeps the file names, not the pictures |
| `/btw` | `/btw <question>` asks a side question with the conversation as context; neither the question nor the answer is added to the history, and the cached start of the request is reused |
| `/goal` | `/goal <objective> --verify <command>` runs the command whenever the agent says it is done and sends failures back until it exits with status 0; `/goal`, `/goal pause`, `/goal resume`, `/goal clear` |
| `/thinking` | Toggle inline thinking display (on/off, persists to config.yml) |
| `/activity` | Toggle activity indicator widget (on/off, persists to config.yml) |
| `/quit` | Exit (aliases: `/exit`, `/q`) |

## Built-in Tools

The registry assembles 31 built-in tools. Bash, file, search, task, web and agent
tools are always present. `Parism` appears when the bundled Parism MCP
package is reachable. The LSP and symbol tools appear only when a compatible
language server is installed; with none detected they are simply omitted from
the registry. The three external project tools stay unregistered until
`external_projects_enabled: true` is set in the config file.

| Tool | Type | Description |
|------|------|-------------|
| `Bash` | Write | Execute shell commands |
| `FileRead` | Read | Read file contents, prefixing each line with `N#hhhhhh` (line number and content hash) and recording the file's digest for the session; binary files are reported by type and size only |
| `FileWrite` | Write | Create a file, or overwrite one that was read in this session and has not changed since |
| `FileEdit` | Write | String or anchor (`N#hhhhhh`) replacement; refused when the file changed since it was read |
| `Glob` | Read | File pattern matching |
| `Grep` | Read | Content search with regex |
| `TodoWrite` | Write | Maintain the task list the agent works through |
| `ActivateSkill` | Meta | Load the instructions of a skill from the catalog in the system prompt; the result lists the files the skill bundles. Registered only when at least one skill can be activated by the model. See [docs/skills.md](docs/skills.md) |
| `AskUser` | Meta | Ask the user a clarifying question with 2-4 suggested options plus free text; errors when no user is reachable (one-shot runs, MCP server, subagents) |
| `WebFetch` | Read | Fetch a URL and return its readable text |
| `WebSearch` | Read | Brave Search query; raises at call time when `BRAVE_API_KEY` is unset |
| `Parism` | Write | Structured shell execution with JSON output (44 whitelisted commands) |
| `Agent` | Write | Spawn a sub-agent (general-purpose, Explore, Plan, code-reviewer, git-management, test-writer), in the foreground or the background |
| `Swarm` | Write | Run multiple agents in parallel under a shared concurrency budget |
| `TaskGet` | Read | Inspect the status and result of a background task (finished tasks are also reported automatically) |
| `TaskStop` | Write | Cancel a running agent task |
| `lsp_diagnostics` | Read | Pull workspace and file-level diagnostics from a connected language server |
| `lsp_goto_definition` | Read | Resolve a symbol to its definition location via the language server |
| `lsp_find_references` | Read | List all references to a symbol via the language server |
| `lsp_rename` | Write | Apply a workspace-wide rename refactor via the language server |
| `symbol_overview` | Read | Return the symbol map (classes, functions, variables) for a file or directory |
| `find_symbol` | Read | Locate a symbol by name path and optionally return its full body |
| `find_referencing_symbols` | Read | Find all symbols that reference a given symbol |
| `restart_language_server` | Write | Restart the language server after a toolchain or dependency change |
| `replace_symbol_body` | Write | Replace the complete body of a symbol in one atomic operation |
| `insert_before_symbol` | Write | Insert code immediately before a symbol definition |
| `insert_after_symbol` | Write | Insert code immediately after a symbol definition |
| `safe_delete_symbol` | Write | Delete a symbol after verifying it has no remaining references |
| `ListQueryableProjects` | Read | List all registered external nerdvana projects available for delegation |
| `RegisterExternalProject` | Write | Register an external nerdvana project for subprocess-isolated query delegation |
| `QueryExternalProject` | Read | Delegate a nerdvana query to a registered external project in an isolated subprocess |

## Agent Types

The `Agent` and `Swarm` tools dispatch tasks to one of six built-in agent profiles. Each profile carries its own system prompt, turn limit and tool allow-list, and all of them are applied. `*` admits every tool of the session except spawning, task control and `AskUser`; `@read` admits every read-only tool (LSP lookups, symbol queries, web reads).

| Agent Type | Max Turns | Allowed Tools | Purpose |
|------------|-----------|---------------|---------|
| `general-purpose` | 50 | `*` | Default catch-all agent |
| `Explore` | 12 | `Glob`, `Grep`, `FileRead`, `@read` | Read-only repo exploration and reconnaissance |
| `Plan` | 15 | `Glob`, `Grep`, `FileRead`, `@read` | Read-only plan drafting; pairs with the `planning_gate` setting |
| `code-reviewer` | 12 | `FileRead`, `Grep`, `Glob`, `@read` | Diff and source review with no write capability |
| `git-management` | 20 | `Bash`, `FileRead` | Branch, commit, and merge orchestration via shell |
| `test-writer` | 30 | `*` | Generates and runs tests across the project |

Sub-agents share the session's language server and MCP connections, follow the same permission policy, and cannot spawn further agents. Live tasks stream into the TaskPanel; a background task's result is reported to the model when it finishes, and `TaskStop` cancels one.

## Self-Recovery

| Mechanism | Behaviour |
|-|-|
| Provider recovery | Failures are classified as transient (429, 5xx, timeouts), context limit, authentication or decoding. Transient ones are retried `model.max_retries` times with backoff or the server's `Retry-After`, then the next `model.fallback_models` entry is used (`provider:model` switches provider). A context-limit failure compacts the history once and retries. Nothing is retried after part of the answer has streamed. |
| Stream timeouts | A stream silent for `session.stream_idle_timeout` seconds, or running past `session.stream_total_timeout`, is treated as a transient failure. |
| Todo guard | When the model ends a turn with open `TodoWrite` items, it is asked to continue; after three reminders without progress it stops and reports the open items. Open items are restated after compaction. |
| `context_limit_recovery` | After a `max_tokens` stop, injects a continuation prompt quoting the last request. |
| `json_parse_recovery` | When a tool result fails JSON parsing, asks for valid JSON. |
| `ralph_loop_check` | On `end_turn`, asks the agent to finish `TODO`, `FIXME`, `NotImplemented`, `# 구현 필요` or `# 미구현` markers. End-of-turn hooks may continue a turn at most three times per prompt. |
| Repeat guard | The same tool call with identical arguments draws a warning at the third consecutive repeat and is refused at the fifth (`TaskGet` polling excepted). |
| Output bounds | Tool results are capped at about 30,000 tokens (10,000 for `WebFetch`), keeping head and tail; the full output is saved under `~/.nerdvana/tool-output/`. |

## Permissions and Approval Modes

Every tool call, in the main loop, sub-agents and background agents alike, is decided in this order:

1. `permissions.always_deny` (tool names, glob patterns allowed): refused.
2. Tools excluded by the active mode: refused and hidden from the model.
3. The tool's own refusal (for example a blocked shell command): refused.
4. `permissions.always_allow`: allowed without asking.
5. The mode's trust level: `strict` asks before every write, `balanced` asks before destructive tools and tools that require confirmation, `yolo` asks nothing.

| `--approval-mode` | Mode profile | Trust | Effect |
|-|-|-|-|
| `default` | `interactive` | balanced | Normal interactive use |
| `auto_edit` | `editing` | balanced | Same tools, editing-oriented prompt |
| `yolo` | `one-shot` | yolo | No confirmations |
| `plan` | `planning` | strict | Write tools and `Bash` hidden |

`permissions.mode` in `nerdvana.yml` (`default`, `accept-edits`, `bypass`, `plan`) selects the same profiles when no `--approval-mode` or `session.default_mode` is given. Without a terminal, a question becomes a refusal.

## MCP Server Integration

Connect external [MCP](https://modelcontextprotocol.io/) servers to extend the tool system. Discovered tools are automatically registered as `mcp__{server}__{tool}`.

Servers are declared in JSON, not in `nerdvana.yml`. Two files are read, global
first and project second, so a project entry overrides a global one of the same
name:

| File | Scope |
|-|-|
| `~/.nerdvana/mcp.json` | Global, applies to every project |
| `<cwd>/.mcp.json` | Project-local (the same filename Claude Code uses) |

Both use the `mcpServers` object:

```json
{
  "mcpServers": {
    "my-server": {
      "transport": "stdio",
      "command": "node",
      "args": ["server.js"],
      "env": { "API_KEY": "${MY_API_KEY}" }
    }
  }
}
```

`nerdvana mcp add`, `nerdvana mcp list`, and `nerdvana mcp remove` edit these
files for you.

Use `/mcp` in the REPL to check connection status, and `/tools` to see all available tools including MCP-discovered ones.

## NIRNA.md — Project Instructions

NerdVana CLI supports `NIRNA.md` files for injecting project-specific instructions into the system prompt (analogous to Claude Code's `CLAUDE.md`).

Discovery order (ascending priority):
1. `~/.nerdvana/NIRNA.md` — global user instructions
2. `<cwd>/NIRNA.md` — project instructions (checked in)
3. `<cwd>/NIRNA.local.md` — local instructions (gitignored)

`AGENTS.md` and `CLAUDE.md` in the project root are loaded after these. When a tool first touches a file in a subdirectory, rule files (`NIRNA.md`, `AGENTS.md`, `CLAUDE.md`) found between that directory and the project root are injected once, nearest first, up to 32 KB per session.

Generate a starter file with `/init` in the REPL.

## Configuration

### Environment Variables

Provider and model are chosen with `--provider` / `--model`, the `/provider`
and `/model` REPL commands, or the config file. There is no environment
variable for either: the `NERDVANA_` prefix reaches only the settings listed
under [Environment variables](#environment-variables) above.

```bash
# Runtime locations and behaviour
export NERDVANA_DATA_HOME="$HOME/.nerdvana"   # user data root
export NERDVANA_CONFIG="$HOME/.nerdvana/config.yml"
export NERDVANA_NO_UPDATE_CHECK=1             # skip the startup version check

# API keys (auto-detected per provider)
export ANTHROPIC_API_KEY="sk-ant-..."
export OPENAI_API_KEY="sk-..."
export GEMINI_API_KEY="..."
export GROQ_API_KEY="gsk_..."
export OPENROUTER_API_KEY="sk-or-..."
export XAI_API_KEY="xai-..."
export DEEPSEEK_API_KEY="sk-..."
export MISTRAL_API_KEY="..."
export CO_API_KEY="co-..."
export TOGETHER_API_KEY="..."
export MOONSHOT_API_KEY="..."
export DASHSCOPE_API_KEY="..."
export MINIMAX_API_KEY="..."
export PERPLEXITY_API_KEY="..."
export FIREWORKS_API_KEY="..."
export CEREBRAS_API_KEY="..."

# Web search (the WebSearch tool errors at call time without it)
export BRAVE_API_KEY="..."
```

### Config File (`nerdvana.yml`)

```yaml
model:
  provider: anthropic  # or openai, gemini, groq, ollama, etc.
  model: claude-sonnet-5-5
  api_key: ""  # leave empty to use env var
  base_url: ""  # override API endpoint
  max_tokens: 8192
  temperature: 1.0
  max_retries: 2             # same-model retries on a transient failure
  prompt_caching: true       # reuse the processed prompt prefix between requests
  fallback_models:           # tried in order once retries are spent
    - claude-opus-5-5
    - openai:gpt-4.1         # provider:model switches provider
  extended_thinking: false   # thinking on Anthropic models where it is optional (Claude 5 models think by default)
  thinking_budget: 8192      # token budget on models that take a manual budget (Haiku 4.5)

permissions:
  mode: default              # default, accept-edits, bypass, plan
  always_allow: []           # tool names or glob patterns, e.g. ["FileRead", "lsp_*", "Bash(git status)"]
  always_deny: []

session:
  persist: true
  max_turns: 200
  max_context_tokens: 180000
  compact_threshold: 0.8
  compact_max_failures: 3    # circuit breaker for consecutive compact failures
  planning_gate: false       # auto-run a Plan agent when a request looks complex
  update_check: true         # set to false to suppress the startup update notice
  default_context: standalone
  default_mode: interactive
  show_activity: true
  stream_idle_timeout: 300   # seconds of provider silence before giving up on a request
  stream_total_timeout: 3600
  post_edit_diagnostics: true
  max_parallel_agents: 5     # sub-agents at once per provider

hooks:
  allow_project_hooks: false # project hooks and project skills; see docs/hooks.md and docs/skills.md

skills:
  include_claude_skills: false  # also read ~/.claude/skills and ./.claude/skills

agents:
  categories: {}                # category -> model for sub-agents, e.g. quick: claude-haiku-4-5-20251001

goal:
  verify_timeout: 300           # seconds before a verification command is stopped
  max_attempts: 5               # failed verifications before a goal is given up
  output_tail_chars: 4000       # how much of a failing output the model sees

sandbox:
  mode: off                     # off | auto | require: confine Bash writes with the OS (Linux Landlock)
  network: true                 # false also refuses TCP connections (Linux 6.7+)
  write_paths: []               # writable besides the project and temp directories

checkpoint:
  enabled: true
  per_session_max: 50

# Register the three external project tools. Off by default: they hand a
# registered directory to a read-capable subprocess.
external_projects_enabled: false
```

Config search order. The first file that exists wins:
1. `--config` flag
2. `NERDVANA_CONFIG` env var
3. `./nerdvana.yml` (current directory)
4. `./nerdvana.yaml` (current directory)
5. `~/.nerdvana/config.yml`
6. `~/.config/nerdvana-cli/config.yml` (legacy location, read for backwards compatibility)

## Local Models (Ollama / vLLM)

```bash
# Ollama — pull a model first
ollama pull qwen3
nerdvana --provider ollama --model qwen3

# vLLM — start server first
vllm serve Qwen/Qwen3-32B
nerdvana --provider vllm --model Qwen/Qwen3-32B
```

## Development

```bash
# Install dev dependencies
pip install -e ".[dev]"

# Run tests
pytest

# Lint
ruff check nerdvana_cli/

# Type check
mypy nerdvana_cli/
```

## Documentation

| Document | Description |
|-|-|
| [docs/configuration.md](docs/configuration.md) | Full config reference |
| [docs/hooks.md](docs/hooks.md) | Hook event system and bridge protocol |
| [docs/agents.md](docs/agents.md) | Agent types, tool budgets, and swarm patterns |
| [docs/mcp-quota.md](docs/mcp-quota.md) | MCP server per-tenant quota config schema |
| [docs/testing-live.md](docs/testing-live.md) | Live provider test matrix and secrets setup |
| [docs/adr/0004-lsp-cache-strategy.md](docs/adr/0004-lsp-cache-strategy.md) | ADR: LSP result cache strategy |
| [CHANGELOG.md](CHANGELOG.md) | Release notes |

## Changelog

Release notes are tracked in [CHANGELOG.md](CHANGELOG.md).

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for environment setup and PR gate details.
Key commands:

```bash
uv sync --extra dev --extra mcp
pre-commit install
uv run pytest -m "not lsp_integration and not live" -q
```

Secrets policy for live tests: [docs/security.md](docs/security.md).

## License

MIT

## Author

최진호 (jinho@nerdvana.kr)
