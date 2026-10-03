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

- **Max turns:** 12
- **Allowed tools:** `Glob`, `Grep`, `FileRead`
- **System prompt:** "You are an exploration agent. Use search and read tools to answer questions about the codebase. Do not write or edit files. Return a concise factual report."
- **Use:** fast read-only codebase exploration.

### `Plan`

- **Max turns:** 15
- **Allowed tools:** `Glob`, `Grep`, `FileRead`
- **System prompt:** "You are an architect agent. Analyze the codebase and produce a structured implementation plan. Do not write code — only plan."
- **Use:** architecture planning without side effects.

### `code-reviewer`

- **Max turns:** 12
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

## What a sub-agent may write

`write_scope` narrows what the sub-agents of an agent type can change, in two ways at once:

| Value | Commands (`Bash`) | File and symbol edit tools |
|-|-|-|
| unset or `project` | the session's `sandbox` policy | the session's permissions |
| `none` | nothing writable, not even the project or `/tmp` | every edit refused |
| a list of paths | only those paths and the temporary directories | only those paths, relative to the project |

The built-in `Explore`, `Plan` and `code-reviewer` agents use `none`. Commands are confined by the
operating system (Landlock, see [sandbox.md](sandbox.md)) and a scope turns `sandbox.mode` from `off` to
`auto` for that agent, so on a system without Landlock the commands run unconfined and only the edit tools are
held to the scope. A session whose `sandbox.mode` is `require` keeps it. The edit tools run in the application and
are held to the scope by the tool executor, with relative paths resolved against the project and `..` and
look-alike directory names refused. A refused edit is counted as `out_of_scope` in the run's signals.

## Isolation in a git worktree

`Agent` with `isolation: worktree` runs the sub-agent in its own `git worktree`: a checkout of the current commit on a
new branch `nerdvana/<description>-<id>` in a temporary directory. Whatever the agent edits or runs stays there, and
the project directory is not touched. When the agent finishes, the worktree is removed if it changed nothing; if it
did, it is kept and the result names the branch and the path, with the command to review the changes. Nothing is merged
for you. Uncommitted changes of the project are not in the copy, which starts from `HEAD`. With the sandbox on, the
agent's commands may also write to the git directories that a commit in the worktree needs. It needs a git repository
with at least one commit.

## Sharing the cost limit

With `session.max_cost_usd` set, a sub-agent does not get the whole limit. Each `Agent` call is
given `session.subagent_budget_fraction` (0.5) of what is still unspent and unpromised, so the first of
several parallel agents gets half, the next a quarter, and together they cannot promise the same money
twice. A `Swarm` takes one share and splits it between its tasks. A sub-agent that reaches its share
stops and returns what it has, ending with a note that the result is partial. When it finishes, its
actual spend is charged to the session and the unused part goes back. A sub-agent on a model with no
known price cannot be bounded this way; see `session.require_price` and `session.max_total_tokens`.

## Running out of turns

Every sub-agent gets a reminder at 60% of its turn limit: the turn budget is stated and the
agent is told to stop exploring and answer with what it has found, saying plainly what it
could not find. At the limit the run stops. Exploration agents have low limits because each
request carries the whole conversation, so a long search costs more than it finds.

## Custom agent types

Drop YAML files into `<cwd>/.nerdvana/agents/` in your project root. Each file
defines one agent type.

`AgentTool` reads that project directory only. `~/.nerdvana/agents/` is created
alongside the other user-data directories and is reserved for a future global
scope; definitions placed there are not loaded today.

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
write_scope: none                   # optional — what it may write: none, project (default) or a list such as [tests, docs/api]
network: false                      # optional, false also refuses TCP connections of its commands (Linux 6.7+); true does not widen a sandbox.network: allowlist session
model: claude-opus-5-5              # optional — "model" or "provider:model"; default is the parent's model
category: deep                      # optional — model taken from agents.categories when no model is set
system_prompt: |                    # optional — injected into child's system prompt
  You are a security expert.
  Audit code for OWASP Top 10 vulnerabilities.
```

### Choosing the model

A sub-agent runs on its parent's model unless something names another one. The
first of these that is set wins:

1. the `model` argument of the `Agent` tool call (or of a `Swarm` task)
2. the `model` of the agent definition
3. the model mapped by `agents.categories` to the `category` argument of the call, or to the `category` of the agent definition

A model is written `model` for the parent's provider or `provider:model` for
another one, the form `model.fallback_models` uses. A different provider is used
only when its API key is present in the environment; otherwise the sub-agent keeps
the parent's model and a warning is logged.

```yaml
# nerdvana.yml
agents:
  categories:
    quick: claude-haiku-4-5-20251001
    deep: claude-opus-5-5
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

- `allowed_tools: ["*"]`: wildcard. Every candidate tool: the standard file, search and shell tools plus the session's LSP, symbol, web, todo and MCP tools.
- `allowed_tools: []`: empty, returns a registry with **zero** tools. The agent will receive the request but have no tools to call.
- `allowed_tools: ["FileRead", "Grep"]`: exact name matching. Mistakes like `"Read"` (pre-Phase-B name) will silently filter out `FileRead`.
- `"@read"`: adds every candidate whose category is READ or SYMBOLIC (LSP lookups, symbol queries, web reads). `Explore`, `Plan` and `code-reviewer` use it. MCP tools declare WRITE, so they are only admitted by name or by the wildcard.
- `Agent`, `Swarm`, `TaskGet`, `TaskStop`, `AskUser` and other META tools are **never** included in subagent registries, so subagents cannot recursively spawn more agents.

---

## Agent loop internals

The tool registry and the top-level loop are built in one place,
`nerdvana_cli/cli/bootstrap.py`: `nerdvana run`, the TUI and `nerdvana review`
each describe their own session, extra tools and callbacks (or agent
definition) and call it. The bootstrap also hands the loop the factories it
cannot import from `core`: the sub-agent runner, the sub-agent registry and the
ToolSearch tool (`LoopFactories` in `nerdvana_cli/core/loop/subagent_config.py`).

The loop keeps the request cycle and hands its other concerns to collaborators
in the subpackages of `nerdvana_cli/core/` (see [architecture.md](architecture.md)):

| Module | Class or functions | Concern |
|-|-|-|
| `loop/run_limits.py` | `RunLimits` | token and cost totals, sub-agent roll-up, the cost and token limits |
| `loop/model_failover.py` | `ModelFailover` | advice and escalation, retry, fallback, the non-streaming resend, the way back to the prompt's model |
| `loop/advisor.py` | `Advisor` | the consultations of the `Advisor` tool and of the signal-triggered advice: what is sent, the call cap, the cost (see [advisor.md](advisor.md)) |
| `loop/phase_effort.py` | `PhaseEffort`, `phase_level` | reasoning effort per phase: planning, implementation, verification |
| `context/server_compaction.py` | `ServerCompaction` | asking the provider to compact the history, with `core/context/compact.py` as the fallback |
| `state/compaction_block.py` | `last_compaction_index` | where in the history a provider's compaction block stands |
| `loop/goal_gate.py` | `GoalGate` | the session goal and the verification that decides whether the run may end |
| `loop/input_queue.py` | `InputQueue` | text typed while the agent works |
| `state/rewind.py` | `Rewinder` | prompt marks and `/rewind` |
| `loop/plan_gate.py` | `plan_for`, `draft_plan` | the planning gate's plan sub-agent |
| `context/loop_context.py` | `provider_messages`, `background_reports`, `open_todos_note`, `session_start_context`, `prepare_tools`, `new_provider` | messages and prompt text the loop adds around the history, the tool list of a run and the provider adapter built from the settings |

`ToolExecutor` (`execution/tool_executor.py`) likewise leaves the permission check to
`PermissionGate` and `ask_user_permission` (`safety/tool_permission.py`) and the edit
scope, goal scope and pre-edit checkpoint to `safety/edit_guard.py`.

`AgentLoop._loop` (in `nerdvana_cli/core/loop/agent_loop.py`) delegates to four focused helpers:

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
that the UI layer consumes to update the status bar. The context step first offers
the job to the provider (`ServerCompaction`, `model.anthropic_compaction: on`); this
method is the client-side compaction that follows when the provider cannot or fails. Runs AI compaction via
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
  to per-area handlers in `nerdvana_cli/ui/slash/`.

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
