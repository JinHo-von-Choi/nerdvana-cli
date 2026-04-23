<p align="center">
  <img src="docs/logo.png" alt="NerdVana CLI" width="480">
</p>

<p align="center">
  <strong>AI-powered CLI development tool — 13 AI platforms, one interface</strong>
</p>

<p align="center">
  <a href="#installation"><img src="https://img.shields.io/badge/install-one--line-blue?style=flat-square" alt="Install"></a>
  <img src="https://img.shields.io/badge/python-%3E%3D3.11-3776AB?style=flat-square&logo=python&logoColor=white" alt="Python">
  <img src="https://img.shields.io/badge/providers-15-green?style=flat-square" alt="Providers">
  <img src="https://img.shields.io/badge/license-MIT-yellow?style=flat-square" alt="License">
  <img src="https://img.shields.io/badge/version-1.1.1-orange?style=flat-square" alt="Version">
  <a href="https://github.com/JinHo-von-Choi/nerdvana-cli"><img src="https://img.shields.io/github/stars/JinHo-von-Choi/nerdvana-cli?style=flat-square&color=brightgreen" alt="Stars"></a>
  <img src="https://img.shields.io/badge/LSP--CI-passing-green?style=flat-square" alt="LSP CI">
</p>

<p align="center">
  Anthropic Claude &middot; OpenAI &middot; Google Gemini &middot; Groq &middot; DeepSeek &middot; Mistral &middot; Ollama &middot; and more
</p>

---

## Features

- **Multi-Provider Support** — works with 13 AI platforms from one CLI
- **Interactive REPL** — conversational coding with streaming output and a live TaskPanel for background agents
- **Non-interactive mode** — single prompt execution for scripting
- **Phase A — Edit Quality** — `FileEdit` enforces line/anchor verification via HashLine and `anchor_hash`, with optional LSP-backed diagnostics, goto-definition, find-references, and rename
- **Phase B — Multi-Agent Swarm** — first-class `Agent`, `Swarm`, `TeamCreate`, `SendMessage`, `TaskGet`, and `TaskStop` tools with concurrent execution budgets and a TaskPanel UI
- **Phase C — Self-Recovery Hooks** — built-in lifecycle hooks (`context_limit_recovery`, `json_parse_recovery`, `ralph_loop_check`) that auto-resume max-token stops, repair JSON tool errors, and chase down TODO/FIXME/NotImplemented markers; optional planning gate, fallback models, and extended thinking
- **Tool System** — Bash, FileRead, FileWrite, FileEdit, Glob, Grep, Parism, Agent, Swarm, TeamCreate, SendMessage, TaskGet, TaskStop, plus four LSP tools
- **MCP Integration** — connect external MCP servers for additional tools (`mcp__{server}__{tool}`)
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
| **Featherless AI** | featherless-llama-3-70b | `FEATHERLESS_API_KEY` |
| **Xiaomi MiMo** | mimo-v2.5-pro | `MIMO_API_KEY` |
| **Ollama** | qwen3 | `OLLAMA_API_KEY` |
| **vLLM** | Qwen/Qwen3-32B | `OPENAI_API_KEY` |
| **DeepSeek** | deepseek-chat | `DEEPSEEK_API_KEY` |
| **Mistral** | mistral-medium-latest | `MISTRAL_API_KEY` |
| **Cohere** | command-r-plus | `CO_API_KEY` |
| **Together AI** | Llama-3.3-70B-Instruct-Turbo | `TOGETHER_API_KEY` |
| **ZAI (GLM)** | glm-4.7 | `ZHIPUAI_API_KEY` |

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
  ├── agents/        — Global agent definitions
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

### Migration

On first run after upgrading, the CLI moves any data from `~/.nerdvana-cli/sessions/` and `~/.config/nerdvana-cli/` into `~/.nerdvana/`. A `.migrated` sentinel prevents reruns.

## Commands

| Command | Description |
|---------|-------------|
| `nerdvana` | Start interactive REPL |
| `nerdvana run "prompt"` | Run single prompt |
| `nerdvana providers` | List all supported providers |
| `nerdvana --version` | Show version |

### REPL Slash Commands

| Command | Description |
|---------|-------------|
| `/help` | Show help |
| `/clear` | Clear chat |
| `/init` | Generate NIRNA.md |
| `/model` | Show/change model |
| `/models` | List available models |
| `/provider` | Add/switch provider |
| `/mode` | Activate/deactivate mode profile |
| `/context` | Set context profile |
| `/mcp` | MCP server status |
| `/tokens` | Show token usage |
| `/skills` | List available skills |
| `/tools` | List tools |
| `/update` | Check and install updates |
| `/memories` | List project memories |
| `/undo` | Restore pre-edit git checkpoint |
| `/redo` | Re-apply last undone checkpoint |
| `/checkpoints` | List session checkpoints |
| `/route-knowledge` | Classify content → suggest WriteMemory scope |
| `/dashboard` | Toggle observability dashboard |
| `/health` | Show 7-day tool call health summary |
| `/quit` | Exit (aliases: `/exit`, `/q`) |

## Built-in Tools

| Tool | Type | Description |
|------|------|-------------|
| Bash | Write | Execute shell commands |
| FileRead | Read | Read file contents |
| FileWrite | Write | Create/overwrite files |
| FileEdit | Write | String replacement with HashLine line verification and optional `anchor_hash` integrity checks |
| Glob | Read | File pattern matching |
| Grep | Read | Content search with regex |
| Parism | Write | Structured shell execution with JSON output (44 whitelisted commands) |
| Agent | Write | Spawn an autonomous sub-agent (general-purpose, Explore, Plan, code-reviewer, git-management, test-writer) |
| Swarm | Write | Run multiple agents in parallel under a shared concurrency budget |
| TeamCreate | Write | Create a named, reusable team of agents (`TeamCreate` registers, `Swarm` dispatches) |
| SendMessage | Write | Send a message between running agents in a swarm/team |
| TaskGet | Read | Inspect the status, transcript, and result of a running or finished task |
| TaskStop | Write | Cancel a running agent task |
| LSP Diagnostics (`lsp_diagnostics`) | Read | Pull workspace and file-level diagnostics from a connected language server |
| LSP Goto Definition (`lsp_goto_definition`) | Read | Resolve a symbol to its definition location via the language server |
| LSP Find References (`lsp_find_references`) | Read | List all references to a symbol via the language server |
| LSP Rename (`lsp_rename`) | Write | Apply a workspace-wide rename refactor via the language server |

LSP tools are registered when a compatible language server is available; if none is detected they degrade gracefully and are simply omitted from the registry.

## Agent Types

The `Agent` and `Swarm` tools dispatch tasks to one of six built-in agent profiles. Each profile defines a default turn budget and an allow-listed tool set. `*` means the agent inherits the full tool registry.

| Agent Type | Max Turns | Allowed Tools | Purpose |
|------------|-----------|---------------|---------|
| `general-purpose` | 50 | `*` | Default catch-all agent with access to every registered tool |
| `Explore` | 20 | `Glob`, `Grep`, `FileRead`, `Bash` | Read-only repo exploration and reconnaissance |
| `Plan` | 20 | `Glob`, `Grep`, `FileRead`, `Bash` | Read-only plan drafting; pairs with the `planning_gate` setting |
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

Configure servers in `nerdvana.yml` or `.nerdvana/mcp.json`:

```yaml
mcp:
  servers:
    my-server:
      transport: stdio
      command: node
      args: ["server.js"]
      env:
        API_KEY: "${MY_API_KEY}"
```

Use `/mcp` in the REPL to check connection status, and `/tools` to see all available tools including MCP-discovered ones.

## NIRNA.md — Project Instructions

NerdVana CLI supports `NIRNA.md` files for injecting project-specific instructions into the system prompt (analogous to Claude Code's `CLAUDE.md`).

Discovery order (ascending priority):
1. `~/.config/nerdvana-cli/NIRNA.md` — global user instructions
2. `<cwd>/NIRNA.md` — project instructions (checked in)
3. `<cwd>/NIRNA.local.md` — local instructions (gitignored)

Generate a starter file with `/init` in the REPL.

## Configuration

### Environment Variables

```bash
# Provider selection
export NERDVANA_PROVIDER="anthropic"  # or openai, gemini, groq, etc.
export NERDVANA_MODEL="claude-sonnet-4-20250514"
export NERDVANA_MAX_TOKENS=8192

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
```

Config search order:
1. `--config` flag
2. `NERDVANA_CONFIG` env var
3. `./nerdvana.yml` (current directory)
4. `~/.config/nerdvana-cli/config.yml`

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

## Changelog

Release notes are tracked in [CHANGELOG.md](CHANGELOG.md). The full Phase A (Edit Quality), Phase B (Multi-Agent Swarm), and Phase C (Self-Recovery Hooks) implementation plans live under [`docs/superpowers/plans/`](docs/superpowers/plans/).

## License

MIT

## Author

최진호 (jinho@nerdvana.kr)
