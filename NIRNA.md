# NIRNA.md

## Build, Test, Lint
- Install: `pip install -e ".[all]"` (all providers) or `pip install -e ".[anthropic]"` (specific)
- One-line install: `curl -fsSL https://raw.githubusercontent.com/JinHo-von-Choi/nerdvana-cli/main/install.sh | bash`
- Dev install: `pip install -e ".[dev]"`
- Test: `pytest` (pytest-asyncio auto mode, 897 tests)
- Lint: `ruff check .` (line-length 120, 0 violations)
- Format: `ruff format .`
- Type check: `mypy nerdvana_cli/ --ignore-missing-imports` (strict, Python 3.11)

## Architecture
- 13 AI providers with unified BaseProvider Protocol (OpenAIProvider covers 10, Anthropic/Gemini separate)
- Tool pipeline: parse_args -> check_permissions (ALLOW/DENY/ASK) -> validate_input -> call
- Concurrency: read-only tools parallel (asyncio.gather), write tools serial
- Session: JSONL append-only logs, messages recorded after API response
- State: SessionState mutable (state.messages.append), context compaction at threshold
- TUI: Textual App (ui/app.py) with ChatMessage widgets (click-to-copy), AgentLoop is backend
- MCP: config -> client -> tools -> manager, stdio + HTTP transport, failure isolation per server
- Parism: structured shell output via MCP, falls back silently to Bash on failure
- Hooks: HookEngine with SESSION_START/BEFORE_TOOL/AFTER_TOOL events, builtin context injection
- Skills: markdown-based prompt plugins (.nerdvana/skills/*.md), /trigger activation
- Agents: 6 builtin types (general-purpose, Explore, Plan, code-reviewer, git-management, test-writer) dispatched via AgentTool, allowed_tools allowlist filters tool registry per-agent
- TaskPanel: Textual widget (ui/task_panel.py) renders live AgentTool/SwarmTool sub-task progress in the right pane, driven by TaskState updates from agent_loop
- LSP: LspClient (core/lsp_client.py) speaks JSON-RPC 2.0 over stdio to language servers; 4 tools (lsp_diagnostics, lsp_goto_definition, lsp_find_references, lsp_rename) registered with graceful degradation when no server is available
- Recovery hooks: agent_loop wraps provider calls with context_limit_recovery, json_parse_recovery, ralph_loop_check, and _is_retryable_error to recover from transient errors and runaway loops without crashing the session
- Planning gate: opt-in two-phase mode (planning_gate=true in YAML) that forces a Plan agent pass before code execution; child agents always run with planning_gate=False to prevent recursion
- Model fallback: providers/base.py exposes fallback_models so a failed primary call cascades through alternates before raising
- Custom agents: .nerdvana/agents/*.yml loaded by agents/registry.py at startup, merged on top of builtin definitions
- Ultrawork: long-running mode that keeps the agent loop alive across HookContext.stop_reason transitions for multi-step refactors

## Key Components
- core/agent_loop.py: streaming agent loop, tool execution, context compaction, recovery hooks (planning_gate, context_limit_recovery, json_parse_recovery, ralph_loop_check, _is_retryable_error, ultrawork)
- core/compact.py: compaction strategy module shared by agent_loop and SessionState
- core/lsp_client.py: stdio JSON-RPC 2.0 LspClient, request/response correlation, capability negotiation
- core/hooks.py: HookEngine event system, HookContext (with stop_reason field, default None)
- core/builtin_hooks.py: session start context injection (tools/settings/NIRNA.md)
- core/skills.py: SkillLoader with 3-tier discovery (builtin < global < project)
- core/updater.py: GitHub release check, self-update via git pull
- agents/builtin.py: 6 builtin agent definitions (general-purpose, Explore, Plan, code-reviewer, git-management, test-writer) with system prompts and allowed_tools
- agents/registry.py: agent registry, .nerdvana/agents/*.yml custom loader, allowed_tools filtering
- providers/base.py: BaseProvider Protocol, ProviderConfig, MODEL_CONTEXT_WINDOWS, fallback_models
- providers/factory.py: create_provider(), resolve_api_key()
- tools/bash_tool.py: regex-based dangerous command blocking, sudo detection
- tools/file_tools.py: path traversal defense via _validate_path(), FileEdit anchor_hash verification (HashLine)
- tools/search_tools.py: path traversal defense via _validate_search_path()
- tools/agent_tool.py: dispatches sub-agent runs through AgentLoop, surfaces progress to TaskPanel
- tools/lsp.py: 4 LSP tool classes (lsp_diagnostics, lsp_goto_definition, lsp_find_references, lsp_rename), silent skip when LspClient unavailable
- tools/team_tools.py: team coordination tools (broadcast/handoff) for multi-agent workflows
- tools/swarm_tool.py: parallel sub-agent dispatch with TaskState aggregation
- ui/app.py: Textual TUI, ChatMessage (click-to-copy), context usage bar, tool spinner, TaskPanel mount
- ui/task_panel.py: TaskPanel widget rendering live sub-task tree from TaskState
- ui/clipboard.py: cross-platform clipboard (xclip/pbcopy/clip.exe)

## Code Style
- Line length 120 (ruff)
- Type hints required (mypy strict, disallow_untyped_defs)
- Async/await throughout for provider calls and tool execution
- Imports: nerdvana_cli as first-party (ruff.isort known-first-party)
- Tools fail-closed by default
- Rich markup in tool_info must be escaped with replace("[", "\\[")

## Env Vars & Setup
- API keys: provider-specific env vars (ANTHROPIC_API_KEY, OPENAI_API_KEY, etc.)
- Config search: --config flag -> NERDVANA_CONFIG env -> ./nerdvana.yml -> ~/.config/nerdvana-cli/config.yml
- Config file permissions: 0o600 (API key protection)
- First-run: auto-triggers interactive setup wizard if no config/API key
- Parism: optional Node.js dependency, falls back to Bash if unavailable
- MCP servers: .mcp.json (project) + ~/.config/nerdvana-cli/mcp.json (global)

## Context Window
- Auto-resolved per model via MODEL_CONTEXT_WINDOWS in providers/base.py
- Fallback to PROVIDER_CAPABILITIES max_context per provider
- User can override in YAML: session.max_context_tokens
- Compaction at compact_threshold (default 0.8): drops oldest messages, keeps recent 10

## Skills
- Built-in: /review, /debug, /explain (nerdvana_cli/skills/)
- Global: ~/.config/nerdvana-cli/skills/*.md
- Project: .nerdvana/skills/*.md (highest priority, overrides same-name)
- Format: YAML frontmatter (name, description, trigger) + markdown body
- Activation: /trigger injects skill body into next system prompt (one-shot)

## Slash Commands
/clear, /exit, /help, /init, /mcp, /model, /models, /provider, /q, /quit, /skills, /tokens, /tools, /update, /<skill-trigger>

## Gotchas
- Provider auto-detection by model name prefix (claude- -> Anthropic, gpt- -> OpenAI, deepseek -> DeepSeek)
- prompts.py "Tool Usage Judgment" section prevents infinite tool loops on simple questions
- NIRNA.md 3-tier loading: global (~/.config/nerdvana-cli/) < project (NIRNA.md) < local (NIRNA.local.md)
- NIRNA.md is a LOCAL file. Use FileRead to view it, NOT MCP tools
- Ollama/vLLM: no API key needed, but local servers must be running
- ProviderConfig.__repr__ masks API key (first 4 + **** + last 4)
- BashTool uses regex patterns, strips sudo prefix before checking
- Parism format parameter: use output_format in ParismClient.run()
- SESSION_START hook fires only on first message (_session_started flag)
- ChatMessage widgets replace RichLog — click to copy, hover highlight
- planning_gate is opt-in (off by default); enabling forces a Plan agent pass before any code-modifying tools run
- Child agents spawned via AgentTool/SwarmTool are always invoked with planning_gate=False to prevent infinite planning recursion
- _RETRYABLE_PATTERNS in agent_loop._is_retryable_error use word-boundary regex; substring matches like "overload" inside unrelated identifiers will not trigger a retry
- HashLine format is `sha256(line.rstrip("\n"))` per line; FileEdit verifies anchor_hash before applying patches and aborts on mismatch instead of corrupting the file
- AgentTool reloads .nerdvana/agents/*.yml on each session start; edits to YAML require restart, not just rerun
- LSP tools silently skip (return empty result) when no language server is configured or the LspClient handshake fails — never raise to the agent loop
- HookContext.stop_reason defaults to None; ralph_loop_check only considers an iteration "stuck" when stop_reason == "end_turn" with no tool calls, not on other terminations