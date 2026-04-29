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
