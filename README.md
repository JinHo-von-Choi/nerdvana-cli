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
  <img src="https://img.shields.io/badge/version-1.5.0-orange?style=flat-square" alt="Version">
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
- **Phase A — Edit Quality** — `FileEdit` enforces line/anchor verification via HashLine and `anchor_hash`, with optional LSP-backed diagnostics, goto-definition, find-references, and rename
- **Phase B — Multi-Agent Swarm** — first-class `Agent`, `Swarm`, `TeamCreate`, `SendMessage`, `TaskGet`, and `TaskStop` tools with concurrent execution budgets and a TaskPanel UI
- **Phase C — Self-Recovery Hooks** — built-in lifecycle hooks (`context_limit_recovery`, `json_parse_recovery`, `ralph_loop_check`) that auto-resume max-token stops, repair JSON tool errors, and chase down TODO/FIXME/NotImplemented markers; optional planning gate, fallback models, and extended thinking
- **Live activity indicator + think-tag rendering** — `<think>...</think>` blocks from DeepSeek-R1, QwQ, Qwen3-thinking, GLM, Kimi K2.5 thinking, MiniMax M2 are split into a dim italic block; an `ActivityIndicator` widget shows the current phase (idle / thinking / waiting_api / streaming / tool_running) and active tool target.
- **Tool System** — Bash, FileRead, FileWrite, FileEdit, Glob, Grep, Parism, Agent, Swarm, TeamCreate, SendMessage, TaskGet, TaskStop, plus four LSP tools
- **MCP Integration** — connect external MCP servers for additional tools (`mcp__{server}__{tool}`)
- **MCP server per-tenant quota** — `nerdvana serve` supports rpm / rph / daily_tokens / max_concurrent limits per client, configured via `mcp_quota.yml`. See [`docs/mcp-quota.md`](docs/mcp-quota.md).
- **Session Persistence** — JSONL transcripts for resume
- **Auto Provider Detection** — picks the right provider from model name
- **Configurable** — YAML config, environment variables, CLI flags, fallback models, and complexity-triggered planning gate

## Supported Providers

| Provider | Default Model | API Key Env Var |
|----------|--------------|-----------------|
| **Anthropic** | claude-sonnet-4-20250514 | `ANTHROPIC_API_KEY` |
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
nerdvana --provider anthropic --model claude-opus-4-20250514
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
| `nerdvana cost` | Aggregate token usage and USD cost over a time window |

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
| `/redo` | Re-apply last undone checkpoint |
| `/checkpoints` | List session checkpoints |
| `/route-knowledge` | Classify content → suggest WriteMemory scope |
| `/dashboard` | Toggle observability dashboard |
| `/health` | Show 7-day tool call health summary |
| `/thinking` | Toggle inline thinking display (on/off, persists to config.yml) |
| `/activity` | Toggle activity indicator widget (on/off, persists to config.yml) |
| `/quit` | Exit (aliases: `/exit`, `/q`) |

## Built-in Tools

The registry assembles 31 built-in tools. Bash, file, search, task, web, agent,
and team tools are always present. `Parism` appears when the bundled Parism MCP
package is reachable. The LSP and symbol tools appear only when a compatible
language server is installed; with none detected they are simply omitted from
the registry. The three external project tools stay unregistered until
`external_projects_enabled: true` is set in the config file.

| Tool | Type | Description |
|------|------|-------------|
| `Bash` | Write | Execute shell commands |
| `FileRead` | Read | Read file contents, returning a per-line SHA256 anchor hash |
| `FileWrite` | Write | Create or overwrite files |
| `FileEdit` | Write | String replacement with HashLine line verification and optional `anchor_hash` integrity checks |
| `Glob` | Read | File pattern matching |
| `Grep` | Read | Content search with regex |
| `TodoWrite` | Write | Maintain the task list the agent works through |
| `WebFetch` | Read | Fetch a URL and return its readable text |
| `WebSearch` | Read | Brave Search query; raises at call time when `BRAVE_API_KEY` is unset |
| `Parism` | Write | Structured shell execution with JSON output (44 whitelisted commands) |
| `Agent` | Write | Spawn an autonomous sub-agent (general-purpose, Explore, Plan, code-reviewer, git-management, test-writer) |
| `Swarm` | Write | Run multiple agents in parallel under a shared concurrency budget |
| `TeamCreate` | Write | Create a named, reusable team of agents (`TeamCreate` registers, `Swarm` dispatches) |
| `SendMessage` | Write | Send a message between running agents in a swarm or team |
| `TaskGet` | Read | Inspect the status, transcript, and result of a running or finished task |
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

The `Agent` and `Swarm` tools dispatch tasks to one of six built-in agent profiles. Each profile defines a default turn budget and an allow-listed tool set. `*` means the agent inherits the full tool registry.

| Agent Type | Max Turns | Allowed Tools | Purpose |
|------------|-----------|---------------|---------|
| `general-purpose` | 50 | `*` | Default catch-all agent with access to every registered tool |
| `Explore` | 20 | `Glob`, `Grep`, `FileRead` | Read-only repo exploration and reconnaissance |
| `Plan` | 20 | `Glob`, `Grep`, `FileRead` | Read-only plan drafting; pairs with the `planning_gate` setting |
| `code-reviewer` | 15 | `FileRead`, `Grep`, `Glob` | Diff and source review with no write capability |
| `git-management` | 20 | `Bash`, `FileRead` | Branch, commit, and merge orchestration via shell |
| `test-writer` | 30 | `*` | Generates and runs tests across the project |

Live tasks spawned by these agents stream into the REPL TaskPanel, where you can watch progress, inspect transcripts via `TaskGet`, and cancel runaway work via `TaskStop`.

## Self-Recovery Hooks

NerdVana CLI ships with three built-in lifecycle hooks that keep long agent loops productive without manual intervention:

| Hook | Event | Behavior |
|------|-------|----------|
| `context_limit_recovery` | `AFTER_API_CALL` | When the model stops with `max_tokens`, injects a continuation prompt referencing the most recent user request so the agent can resume its work |
| `json_parse_recovery` | `AFTER_TOOL` | When a tool result fails JSON parsing, injects a correction message naming the offending tool and asking for valid JSON |
| `ralph_loop_check` | `AFTER_API_CALL` | On `end_turn`, scans the assistant message for `TODO`, `FIXME`, `NotImplemented`, `# 구현 필요`, or `# 미구현` markers and asks the agent to finish them before yielding (the "Ralph" self-finishing loop) |

These hooks are registered automatically and form the backbone of Phase C self-recovery. They can be combined with the optional `planning_gate`, `fallback_models`, and `extended_thinking` settings described in [Configuration](#configuration) for fully autonomous runs.

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
  model: claude-sonnet-4-20250514
  api_key: ""  # leave empty to use env var
  base_url: ""  # override API endpoint
  max_tokens: 8192
  temperature: 1.0
  fallback_models:           # tried in order if the primary model errors
    - claude-opus-4-20250514
    - gpt-4.1
  extended_thinking: false   # enable Anthropic extended thinking blocks
  thinking_budget: 8192      # token budget reserved for extended thinking

permissions:
  mode: default
  always_allow: []
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

hooks:
  session_start:
    - builtin:context_injection
  before_tool: []
  after_tool: []
  allow_project_hooks: false # see docs/hooks.md before turning this on

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

Release notes are tracked in [CHANGELOG.md](CHANGELOG.md). The full Phase A (Edit Quality), Phase B (Multi-Agent Swarm), and Phase C (Self-Recovery Hooks) implementation plans live under [`docs/superpowers/plans/`](docs/superpowers/plans/).

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
