# NIRNA.md

## Build, Test, Lint
- Install: pip install -e ".[all]" (all providers) or pip install -e ".[anthropic]" (specific)
- Dev install: pip install -e ".[dev]"
- Test: pytest (pytest-asyncio auto mode)
- Lint: ruff check nerdvana_cli/ (line-length 120)
- Format: ruff format nerdvana_cli/
- Type check: mypy nerdvana_cli/ (strict, Python 3.11)

## Architecture
- 12 AI providers with unified abstraction (OpenAIProvider covers 10, Anthropic/Gemini separate)
- Tool pipeline: parse_args -> check_permissions -> validate_input -> call
- Concurrency: read-only tools parallel, write tools serial
- Session: JSONL append-only logs, messages recorded after API response
- State: SessionState is mutable (state.messages.append), not atomic replacement
- TUI: Textual App (ui/app.py), AgentLoop is backend (core/agent_loop.py)
- Parism: structured shell output preferred over raw Bash when available

## Code Style
- Line length 120 (ruff)
- Type hints required (mypy strict, disallow_untyped_defs)
- Async/await throughout for provider calls and tool execution
- Imports: nerdvana_cli as first-party (ruff.isort known-first-party)
- Tools fail-closed by default

## Env Vars & Setup
- API keys: provider-specific env vars (ANTHROPIC_API_KEY, OPENAI_API_KEY, etc.)
- Config search: --config flag -> NERDVANA_CONFIG env -> ./nerdvana.yml -> ~/.config/nerdvana-cli/config.yml
- First-run: auto-triggers interactive setup wizard if no config/API key
- Parism: optional Node.js dependency, falls back to Bash if unavailable

## Gotchas
- Provider auto-detection by model name prefix (claude- -> Anthropic, gpt- -> OpenAI, deepseek -> DeepSeek)
- prompts.py "Tool Usage Judgment" section prevents infinite tool loops on simple questions
- NIRNA.md 3-tier loading: global (~/.config/nerdvana-cli/) < project (NIRNA.md) < local (NIRNA.local.md)
- Ollama/vLLM: no API key needed, but local servers must be running
- Session compaction at 80% context threshold (compact_threshold: 0.8)
- System prompt built dynamically by prompts.py, tool descriptions from tool.prompt()