# NerdVana CLI

AI-powered CLI development tool — supports **13 AI platforms** including Anthropic Claude, OpenAI, Google Gemini, Groq, Ollama, ZAI (GLM), and more.

## Features

- **Multi-Provider Support** — works with 13 AI platforms from one CLI
- **Interactive REPL** — conversational coding with streaming output
- **Non-interactive mode** — single prompt execution for scripting
- **Tool System** — Bash, FileRead, FileWrite, FileEdit, Glob, Grep, Parism
- **MCP Integration** — connect external MCP servers for additional tools
- **Session Persistence** — JSONL transcripts for resume
- **Auto Provider Detection** — picks the right provider from model name
- **Configurable** — YAML config, environment variables, CLI flags

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
| **vLLM** | Qwen/Qwen3-32B | `OPENAI_API_KEY` |
| **DeepSeek** | deepseek-chat | `DEEPSEEK_API_KEY` |
| **Mistral** | mistral-medium-latest | `MISTRAL_API_KEY` |
| **Cohere** | command-r-plus | `CO_API_KEY` |
| **Together AI** | Llama-3.3-70B-Instruct-Turbo | `TOGETHER_API_KEY` |
| **ZAI (GLM)** | glm-4.7 | `ZHIPUAI_API_KEY` |

## Installation

```bash
# Install with all providers
cd ~/job/nerdvana-cli
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
| `/quit` | Exit REPL |
| `/model` | Show current model |
| `/model <name>` | Change model (auto-detects provider) |
| `/models` | List available models from provider API |
| `/provider` | Show current provider |
| `/provider <name>` | Change provider |
| `/providers` | List all supported providers |
| `/mcp` | Show MCP server connection status |
| `/init` | Generate NIRNA.md project instructions |
| `/setup` | Run interactive setup wizard |
| `/tokens` | Show token usage |
| `/clear` | Clear conversation |
| `/session` | Show session info |
| `/tools` | List available tools |
| `/verbose` | Toggle verbose mode |

## Built-in Tools

| Tool | Type | Description |
|------|------|-------------|
| Bash | Write | Execute shell commands |
| FileRead | Read | Read file contents |
| FileWrite | Write | Create/overwrite files |
| FileEdit | Write | String replacement in files |
| Glob | Read | File pattern matching |
| Grep | Read | Content search with regex |
| Parism | Write | Structured shell execution with JSON output (44 whitelisted commands) |

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

permissions:
  mode: default
  always_allow: []
  always_deny: []

session:
  persist: true
  max_turns: 200
  max_context_tokens: 180000
  compact_threshold: 0.8
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

## License

MIT

## Author

최진호 (jinho@nerdvana.kr)
