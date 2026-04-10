# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog 1.1.0](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning 2.0.0](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.3.0] - 2026-04-10

This release lands three major workstreams (Phase A, Phase B, Phase C) on top of
the initial agent swarm foundation. Test coverage grows from 212 to 272 tests
(+60) across the three phases.

### Added

#### Phase A — Edit Quality (merged 2026-04-10, 212 → 233 tests, +21)

- `FileRead` now emits a 4-character SHA-256 `HashLine` prefix on every line so
  the model can address specific lines unambiguously.
- `FileEdit` accepts an `anchor_hash` parameter that pins an edit to a verified
  line, preventing drift when the file has changed since the previous read.
- New `LspClient` (`nerdvana_cli/core/lsp_client.py`) speaks stdio JSON-RPC 2.0
  to language servers using only the Python standard library — no third-party
  LSP dependency.
- Four new LSP tools wired into the registry with graceful degradation when no
  language server is available:
  - `lsp_diagnostics`
  - `lsp_goto_definition`
  - `lsp_find_references`
  - `lsp_rename`

#### Phase B — Multi-Agent Completion (merged 2026-04-10, 233 → 250 tests, +17)

- New `TaskPanel` Textual widget that polls the `TaskRegistry` every 0.5 s and
  surfaces `current_tool`, `tokens_used`, and a rolling `output_buffer` for
  every running subagent.
- Three new built-in agent types bring the total to six:
  `general-purpose` (50 turns, all tools), `Explore` (20 turns,
  `Glob`/`Grep`/`FileRead`/`Bash`), `Plan` (20 turns,
  `Glob`/`Grep`/`FileRead`/`Bash`), `code-reviewer` (15 turns,
  `FileRead`/`Grep`/`Glob`), `git-management` (20 turns, `Bash`/`FileRead`),
  and `test-writer` (30 turns, all tools).
- `create_subagent_registry()` now actually filters tools by the
  `allowed_tools` list declared on each agent type, replacing the previous
  no-op pass-through.
- `AgentTypeRegistry` auto-loads custom agent definitions from
  `.nerdvana/agents/*.yml`, so users can ship project-local agents without
  touching the codebase.

#### Phase C — Hooks & Workflow (merged 2026-04-10, 250 → 272 tests, +22)

- Complexity detection in `agent_loop.py` inspects incoming prompts for
  multi-step signals; when `settings.session.planning_gate` is enabled the
  Plan subagent runs automatically and its output is injected back into the
  conversation as a synthetic `[Auto-generated plan]` user message.
- Context-recovery hook (`AFTER_API_CALL`) intercepts `max_tokens` stop
  reasons, runs compaction, and resumes the loop without losing the user's
  task.
- `ModelConfig` gains a `fallback` list; when the primary provider returns an
  HTTP error the agent loop transparently switches to the next model in the
  chain.
- High-impact built-in hooks added in `builtin_hooks.py`:
  - **ralph loop** — repeats the last assistant turn until a stop condition.
  - **ultrawork** — multi-pass deep-work driver gated by a module-level
    `_is_ultrawork` predicate (extracted for test isolation).
  - JSON parse helper hook and comment-checker hook.
- `HookContext` now carries a `stop_reason` field so AFTER_API_CALL hooks can
  branch on the upstream provider's termination cause.

### Changed

- The total tool surface available to agents reaches 17 tools when the
  optional `Parism` tool is enabled: `Agent`, `Bash`, `FileEdit`, `FileRead`,
  `FileWrite`, `Glob`, `Grep`, `Parism`, `SendMessage`, `Swarm`, `TaskGet`,
  `TaskStop`, `TeamCreate`, `lsp_diagnostics`, `lsp_find_references`,
  `lsp_goto_definition`, `lsp_rename`.
- Subagents spawned by the auto-planning path inherit a forced
  `planning_gate=False` to prevent recursive planning loops.

### Fixed

- `agent_loop.py` no longer touches the non-existent `ToolRegistry.tools`
  attribute; it now iterates via `self.registry.all_tools()`.
- The fallback model factory call uses the existing
  `self.create_provider_from_settings()` helper instead of the incorrect
  `create_provider(self.settings)` signature from the original plan.

[Unreleased]: https://github.com/nirna/nerdvana-cli/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/nirna/nerdvana-cli/releases/tag/v0.3.0
