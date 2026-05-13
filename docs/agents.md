# Agent Types

NerdVana CLI's `Agent` tool spawns a subagent with an isolated context and a
restricted tool set. The tool filter is enforced at registry level by
`create_subagent_registry(allowed_tools=...)` — an agent that was not granted
`FileWrite` literally does not have the tool in its registry and cannot call it.

## Built-in agent types

Defined in `nerdvana_cli/agents/builtin.py`.

### `general-purpose`

- **Max turns:** 50
- **Allowed tools:** `*` (all standard tools)
- **Use:** open-ended research, multi-step implementation, general delegation.

### `Explore`

- **Max turns:** 20
- **Allowed tools:** `Glob`, `Grep`, `FileRead`
- **System prompt:** "You are an exploration agent. Use search and read tools to answer questions about the codebase. Do not write or edit files. Return a concise factual report."
- **Use:** fast read-only codebase exploration.

### `Plan`

- **Max turns:** 20
- **Allowed tools:** `Glob`, `Grep`, `FileRead`
- **System prompt:** "You are an architect agent. Analyze the codebase and produce a structured implementation plan. Do not write code — only plan."
- **Use:** architecture planning without side effects.

### `code-reviewer`

- **Max turns:** 15
- **Allowed tools:** `FileRead`, `Grep`, `Glob`
- **System prompt:** "You are a code review agent. Read files, search for patterns, and identify bugs, security issues, and style problems. Do not modify files. Return a structured review report."
- **Use:** read-only code quality review. Cannot execute Bash or modify files — safe for review-only workflows.

### `git-management`

- **Max turns:** 20
- **Allowed tools:** `Bash`, `FileRead`
- **System prompt:** "You are a git management agent. Use Bash for git commands only. Do not modify source files directly. Perform git operations: status, add, commit, branch, log, diff."
- **Use:** commits, branches, status, diff, log.

### `test-writer`

- **Max turns:** 30
- **Allowed tools:** `*` (all)
- **System prompt:** "You are a test-writing agent. Write thorough tests using the project's existing test framework. Follow TDD: write failing test first, then implement minimal code to pass. Do not refactor existing code."
- **Use:** TDD test generation and execution.

## Custom agent types

Drop YAML files into `.nerdvana/agents/` in your project root. Each file
defines one agent type.

### File format

```yaml
# .nerdvana/agents/security-auditor.yml
name: security-auditor              # required — agent_type identifier
description: OWASP security audit   # optional — shown in Agent tool schema
max_turns: 25                       # optional — default 50
allowed_tools:                      # optional — default ["*"] (all)
  - FileRead
  - Glob
  - Grep
  - Bash
system_prompt: |                    # optional — injected into child's system prompt
  You are a security expert.
  Audit code for OWASP Top 10 vulnerabilities.
```

### Loading

Custom agents are loaded on **every** `Agent` tool call, not at startup. This
means edits to `.nerdvana/agents/*.yml` take effect on the next invocation
without restarting NerdVana CLI.

Malformed YAML files are silently skipped — check `/verbose` mode logs if an
agent type seems missing.

### Using a custom agent

```
> @Agent subagent_type=security-auditor prompt="Audit the authentication module"
```

Or via the MCP-style tool call from the REPL.

## Tool-filtering semantics

- `allowed_tools: ["*"]` — wildcard, includes every standard tool.
- `allowed_tools: []` — empty, returns a registry with **zero** tools. The agent will receive the request but have no tools to call.
- `allowed_tools: ["FileRead", "Grep"]` — exact name matching. Mistakes like `"Read"` (pre-Phase-B name) will silently filter out `FileRead`.
- `AgentTool` and team tools (`TeamCreate`, `SendMessage`, `TaskGet`, `TaskStop`) are **never** included in subagent registries — subagents cannot recursively spawn more agents or manage teams.

---

## Agent loop internals

`AgentLoop._loop` (in `nerdvana_cli/core/agent_loop.py`) delegates to four focused helpers:

```python
async def _maybe_compact_messages(self, cur_toks: int, thr: int) -> AsyncGenerator[str, None]: ...
def _handle_max_tokens_stop(self) -> bool: ...
def _handle_end_turn_stop(self, asst_text: str, thinking_buffer: str) -> bool: ...
async def _handle_tool_use_stop(
    self, asst_text: str, tool_uses: list[dict], tool_ctx: ToolContext
) -> AsyncGenerator[str, None]: ...
```

### `_maybe_compact_messages`

Async generator. Invoked before each API call when the estimated token count
exceeds the configured threshold. Yields `COMPACT_STATUS_PREFIX` status strings
that the UI layer consumes to update the status bar. Runs AI compaction via
`ai_compact()`; if the circuit breaker is open or `ai_compact` returns `None`,
falls back to naive truncation via `compact_messages()`. Mutates
`self.state.messages` in place.

### `_handle_max_tokens_stop`

Fires `AFTER_API_CALL` hooks with `stop_reason="max_tokens"`. Returns `True` if
any hook injected recovery messages into the history (the loop continues);
`False` if no hook recovered (the loop terminates).

### `_handle_end_turn_stop`

Persists the completed assistant message, records it in the session log, then
fires `AFTER_API_CALL` hooks with `stop_reason="end_turn"`. Returns `True` if a
hook injected continuation messages.

### `_handle_tool_use_stop`

Async generator. Appends the assistant turn (with tool-use blocks) to history,
yields `TOOL_STATUS_PREFIX` markers for each pending call, then dispatches the
full batch via `tool_executor.run_batch(tool_uses, tool_ctx)`. After the batch
completes, yields `TOOL_DONE_PREFIX` markers and appends all tool results to
history.

`ToolResult` (defined in `nerdvana_cli/types/__init__.py`) carries an optional
`tokens: int = 0` field. `AgentTool` populates it from sub-agent usage metadata;
all other tools leave it at the default `0`. The MCP server reads this field via
`getattr(raw_result, "tokens", 0)` after `_call_tool_raw` returns, so quota
accounting reflects actual sub-agent token spend.

---

## UI surface separation

`nerdvana_cli/ui/` is split into three primary concerns:

- `app.py` — `NerdvanaApp(App[object])`: widget composition and event wiring
  only. Does not run the agent loop or parse commands directly.
- `response_runner.py` — `run_response_stream(app, prompt)`: drives the agent
  loop and streams output tokens, tool-status markers, and compaction notices to
  the chat widgets.
- `command_dispatcher.py` — `dispatch_command(app, cmd)`: routes slash commands
  to per-area handlers in `nerdvana_cli/commands/`.

`ui/widgets/` contains one class per file: `ActivityIndicator`, `ChatMessage`,
`CommandMenu`, `ModelSelector`, `MultilineAwareInput`, `ProviderSelector`,
`StatusBar`, `StreamingOutput`, `ToolStatusLine`.

---

## Provider variant metadata

`nerdvana_cli/providers/variants.yml` is the single source of truth for
per-variant capabilities, default base URLs, default model names, and API key
environment variable names. At import time `nerdvana_cli/providers/base.py`
reads this file and projects the data into the four public dicts
(`PROVIDER_CAPABILITIES`, `DEFAULT_BASE_URLS`, `DEFAULT_MODELS`,
`PROVIDER_KEY_ENVVARS`) that the rest of the codebase imports. The public API is
unchanged; only the source of truth moved from inline Python to YAML.

---

## MCP server dispatch sequence

Every tool call on the built-in MCP server (`nerdvana_cli/server/mcp_server.py`)
passes through the following stages in order:

1. **Auth** — `_resolve_identity()` extracts the tenant identifier from the
   active transport context.
2. **ACL** — `ACLManager.check(tenant, tool_name)` enforces role-based access.
   Denied calls are audited and raise `PermissionError` immediately.
3. **Quota** — `QuotaPolicyResolver.resolve(tenant, roles=acl.effective_roles(tenant))`
   selects the applicable quota policy; `QuotaStore.check(tenant, policy)` tests
   the current counters. Denied calls are audited and raise `QuotaExceeded`.
4. **Execute** — `_call_tool_raw(tool_name, args)` dispatches to the concrete
   `BaseTool` implementation.
5. **Release** — `QuotaStore.release(tenant, tokens=result.tokens)` decrements
   in-flight counters and records token spend in the rolling windows.

For the full quota policy schema (rpm, rph, daily_tokens, max_concurrent, tenant
overrides) see [`docs/mcp-quota.md`](mcp-quota.md).
