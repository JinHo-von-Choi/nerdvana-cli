# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog 1.1.0](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning 2.0.0](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.6.0] - 2026-04-18

Phase E: Project Memory + Onboarding + Git Checkpoints.
Agents now manage project-local knowledge via a 4-scope memory system,
perform guided onboarding, and can checkpoint/undo edits via git stash.

### Added

- `core/memories.py` — `MemoryScope` enum (project_rule / project_knowledge /
  user_global / agent_experience), `MemoriesManager` (CRUD, slash namespaces,
  fcntl.flock concurrency, stale-GC, onboarding stamp helpers,
  session_start_hint).
- `core/paths.py` — `project_memories_dir`, `project_onboarding_dir`,
  `global_memories_dir` path helpers.
- `core/checkpoint.py` — `CheckpointManager`: auto-stash before edit tools
  (FileEdit, FileWrite, ReplaceSymbolBody, InsertBefore/AfterSymbol,
  SafeDeleteSymbol), LRU eviction (per_session_max default 50),
  undo() / redo() / list_checkpoints(). Silent no-op outside git repos.
- `core/settings.py` — `CheckpointConfig` (enabled, per_session_max);
  `NerdvanaSettings.checkpoint` field.
- `tools/memory_tools.py` — 9 tools:
  - Memory CRUD: WriteMemory (scope enum + secrets scanner), ReadMemory,
    ListMemories, DeleteMemory, RenameMemory, EditMemory.
  - Onboarding: CheckOnboardingPerformed, Onboarding, InitialInstructions.
- `commands/memory_commands.py` — 5 slash command handlers:
  /undo, /redo, /checkpoints, /memories (--stale --days N), /route-knowledge.
- `core/builtin_hooks.py` — `session_start_memory_hint` hook: injects
  memory count into system prompt at session start (content never auto-injected).

### Security

- WriteMemory runs secrets scanner before storing: blocks OpenAI/Anthropic keys,
  AWS Access Key IDs, GitHub PATs, API_KEY env vars, Authorization Bearer tokens.

## [0.5.1] - 2026-04-18

Phase D.1: Edit symbol tools — insert before/after and safe delete.

### Added

- `tools/symbol_tools.py` — `InsertBeforeSymbolTool` (`insert_before_symbol`):
  two-step diff-preview workflow that inserts code immediately before a symbol
  definition.  Typical uses: decorators, imports above a class, type aliases.
- `tools/symbol_tools.py` — `InsertAfterSymbolTool` (`insert_after_symbol`):
  inserts code immediately after a symbol body end.  Typical uses: sibling
  functions, new methods following an existing one.
- `tools/symbol_tools.py` — `SafeDeleteSymbolTool` (`safe_delete_symbol`):
  deletes a symbol only when `find_references` returns zero hits.  When
  references exist the tool returns `{"status": "blocked_by_references",
  "references": [...]}` without creating a preview or touching the filesystem.
- `core/code_editor.py` — `prepare_insert_before`, `prepare_insert_after`,
  `prepare_safe_delete` methods; `_path_to_uri` module-level helper.
- `_locate_symbol_lines` and `_do_apply` shared async helpers in
  `symbol_tools.py` (eliminate duplicate code between edit tools).
- 12 new unit tests in `test_code_editor.py` and `test_symbol_tools.py`.
- 3 new `@pytest.mark.lsp_integration` E2E tests in
  `tests/lsp/test_symbol_tools_integration.py`.

### Changed

- `create_symbol_tools` factory now returns all 8 symbol tools (was 5).
- `ReplaceSymbolBodyTool._do_apply` delegates to shared `_do_apply` helper.

## [0.5.0] - 2026-04-18

Phase D: Semantic Reading + Minimal Edit + Diff Preview.
Agents now operate at symbol granularity (Python + TypeScript), backed by
pyright and typescript-language-server via the existing LSP client.

### Added

- `core/symbol.py` — `NamePathResolver` (``Foo/bar.baz`` path grammar),
  `SymbolDictGrouper` (LSP DocumentSymbol → kind-grouped compact JSON),
  `LanguageServerSymbol` dataclass (`name_path`, `kind`, `location`,
  `children`), `LanguageServerSymbolRetriever` (high-level API over
  `LspClient`: `get_overview`, `find`, `find_references`).
- `core/code_editor.py` — `PreviewEntry` (NamedTuple capturing
  workspace_edit + per-file SHA256 fingerprints), `CodeEditor`
  (session-scoped preview store: `create_preview` → unified diff,
  `apply` → SHA256 re-validation + LspClient write). LRU eviction at 20
  entries; configurable via `.nerdvana.yml` `preview.lru_max`.
- `core/symbol_graph.py` — `SymbolGraph` Repo Map: top-level + depth-1
  nodes, call-graph edges from `find_references`, `to_compact_json`
  LOC-weighted token-budget serialiser.
- `tools/symbol_tools.py` — 5 new `BaseTool` subclasses:
  - `symbol_overview` (`SYMBOLIC`, `EXTERNAL`)
  - `find_symbol` (`SYMBOLIC`, `EXTERNAL`)
  - `find_referencing_symbols` (`SYMBOLIC`, `EXTERNAL`)
  - `restart_language_server` (`META`, `EXTERNAL`)
  - `replace_symbol_body` (`WRITE`, `FILESYSTEM`, `requires_confirmation=True`)
- `tools/registry.py` — 5 symbol tools registered when a language server
  binary is present (alongside the existing 4 LSP tools).
- Integration test fixtures: `tests/lsp/fixtures/sample_python_project/`
  (`models.py`, `services.py`).
- `tests/test_symbol.py` (19 tests), `tests/test_code_editor.py` (11),
  `tests/test_symbol_graph.py` (8), `tests/test_symbol_tools.py` (20),
  `tests/lsp/test_symbol_tools_integration.py` (lsp_integration marker).

### Changed

- `pyproject.toml` version 0.4.3 → 0.5.0.
- Test count: 505 → 563 (+58 unit), 571 with integration markers collected.

## [0.4.3] - 2026-04-18

Phase 0B foundational hardening. LSP client gaps identified across three
rounds of multi-AI review are closed. Tool registry gains the metadata
layer Phase D/F depend on. CI grows a language-server matrix to keep
symbol-tool regressions out of main.

### Added

- `core/lsp_client.py` now derives `rootUri` from the project cwd as a
  `file://` URI (previously hardcoded `None`).
- `textDocument/didOpen` lifecycle tracking via a new `_open_files`
  version map; every file touched by `definition`/`references`/`rename`
  is opened before the request.
- `workspace/applyEdit` handles `documentChanges` (rust-analyzer,
  clangd) with legacy `changes` fallback.
- Server-specific initialization timeouts
  (pyright/ts-server/gopls 10s, clangd 15s, rust-analyzer 30s,
  jdtls 45s, default 15s) replace the 10-second hardcode.
- `shutdown` LSP request + `exit` notification precede SIGTERM on
  server teardown, preventing orphan language-server processes.
- Workspace edit writes flow through `utils/path.safe_open_fd`, closing
  the symlink TOCTOU window matching `file_tools.py`.
- `core/tool.py` introduces `ToolCategory` (read/write/destructive/
  symbolic/meta), `ToolSideEffect` (none/filesystem/process/network/
  external), and `BaseTool.tags`, `requires_confirmation` class
  variables. All 17 built-in tools classified.
- `ToolRegistry.filter()` predicate API supports
  `category`/`side_effects`/`tags_any`/`tags_all`/`read_only` queries.
  Phase F profile enforcement and Phase D symbol-tool gating build on
  this.
- `.github/workflows/lsp_tests.yml` CI job installs pyright and
  typescript-language-server via npm on ubuntu-22.04 × python
  3.11/3.12, caches `~/.npm`, and runs the `lsp_integration`-marked
  suite separately from the main test job.
- New pytest markers `lsp_integration` and `slow`. `tests/lsp/`
  placeholder suite verifies binaries are on PATH under CI.

### Changed

- `BaseTool.is_read_only` is now a property derived from `category ∈
  {READ, SYMBOLIC}`. Existing callers unchanged; `McpToolAdapter` no
  longer assigns the attribute directly.
- `_apply_workspace_edit` accepts an optional `cwd` parameter so
  callers can scope path resolution; LSP rename supplies
  `self._project_root`.

### Test count

Collected: 485 (0.4.2) → 505 (0.4.3), +20 net new:
- 8 LSP hardening tests
- 10 tool metadata tests
- 2 LSP integration placeholders (CI-only)

## [0.4.2] - 2026-04-18

Phase 0A foundational debt payment. No user-visible feature changes; pure
structural refactor + metadata drift fix. Parity snapshots and fault-injection
suite guarantee behavioral preservation.

### Changed

- `nerdvana_cli/core/agent_loop.py` decomposed from 752 to exactly 400 lines.
  Responsibilities delegated to three new modules:
  - `core/loop_state.py` — immutable `LoopState` dataclass; evolution via
    `.evolve()` replaces scattered field mutations.
  - `core/tool_executor.py` — `ToolExecutor` takes over tool scheduling,
    permission checks, and result serialization. Preserves parallel-read /
    serial-write policy and hook firing order.
  - `core/loop_hooks.py` — `LoopHookEngine` relocates `context_limit_recovery`,
    `json_parse_recovery`, `ralph_loop_check`, and `_is_retryable_error`.

### Added

- `tests/parity/` 8-scenario snapshot suite locks in 0.4.1 `AgentLoop`
  behavior (REPL, streaming, tool chains, session resume, compaction,
  context-limit/ralph-loop recovery). Serves as regression baseline for
  future refactors.
- `tests/parity/test_fault_injection.py` 3-scenario resilience suite
  (provider HTTP 500 retry, tool `asyncio.TimeoutError`, session write
  `OSError(ENOSPC)` integrity).
- `tests/contracts/test_loop_decomposition.py` + `test_loop_hooks.py`
  assert decomposition invariants (LoopState immutability, evolve field
  preservation, hook signature contract).
- `scripts/check_import_graph.py` detects circular imports via
  `networkx.simple_cycles`, with baseline mode (`.import_cycles_baseline.json`
  tracks 8 pre-existing cycles, fails only on new ones).
- `scripts/sync_version_badges.py` + `scripts/sync_test_count.py`
  propagate `pyproject.toml` version and `pytest --collect-only` count
  into README and NIRNA.md.
- `.pre-commit-config.yaml` wires the three scripts plus `ruff` as
  pre-commit hooks (`sync-test-count` runs at pre-push to avoid recursion).

### Fixed

- README version badge drift (`0.3.0` → `0.4.2` after release).
- `NIRNA.md` test count drift (272 → 485).
- Known limitation preserved as-is (fix deferred to a dedicated bug
  ticket): `ralph_loop_check` hook does not fire on `end_turn` stop
  reason in 0.4.1. Captured in parity scenario 8 baseline to prevent
  silent behavior change.

### Test count

Collected: 465 (0.4.1) → 485 (0.4.2), +20 net new:
- 8 parity snapshots
- 9 loop-decomposition contracts
- 3 fault-injection scenarios

## [0.4.1] - 2026-04-16

### Added

- Setup wizard now asks for Ollama mode (local vs cloud) when Ollama is
  selected. Local keeps the default `http://localhost:11434/v1` endpoint
  with no API key; cloud routes to `https://ollama.com/v1` with
  `OLLAMA_API_KEY` and defaults the model to `gpt-oss:120b`.

## [0.4.0] - 2026-04-16

A single-day release that lands four major workstreams on top of 0.3.0:
security hardening, install-layout separation, a responsive opencode-style
sidebar, and a context-injection overhaul. Test count grows from 316 to 463
(+147 net new tests).

### Added

#### Security hardening (316 → 399 tests)

- `bash_tool` denylist extended to block interpreter `-c`/`-e`/`-r`
  execution (`python`, `perl`, `ruby`, `node`, `php`), download-then-exec
  chains (`curl`/`wget` followed by `bash`/`source`/`.`), `rm -rf $HOME`
  / `$PWD` / `$OLDPWD` in every flag permutation, `tee` to block devices,
  `find -delete`, and `find -exec rm`.
- `mcp/client.py` enforces explicit `verify=True` on the HTTP transport,
  caps HTTP/SSE response bodies and stdio lines at 10 MB, and warns on
  `http://` transports to non-loopback hosts.
- `file_tools` routes every `FileRead`/`FileWrite`/`FileEdit` open
  through a new `safe_open_fd` / `safe_makedirs` pair in
  `utils/path.py` that passes `O_NOFOLLOW` on every path segment,
  closing the pre-validation→open TOCTOU window.
- New cross-tool `tests/test_security_integration.py` suite exercising
  bash-created symlinks, disguised compound commands, and MCP response
  path injection.

#### Install layout hardening (399 → 428 tests)

- New `nerdvana_cli/core/paths.py` is the single source of truth for
  every runtime path. `NERDVANA_DATA_HOME` (default `~/.nerdvana`) is
  now distinct from `NERDVANA_HOME` (install root).
- `core/session.py` writes session JSONL files to `~/.nerdvana/sessions/`
  instead of `~/.nerdvana-cli/sessions/`, which previously corrupted
  `git pull --ff-only` in the install directory.
- New `nerdvana_cli/core/migrate.py` runs once on startup, MOVES legacy
  sessions out of the install dir, and COPIES legacy
  `~/.config/nerdvana-cli/{config.yml, NIRNA.md, mcp.json, skills/,
  hooks/, agents/}` into `~/.nerdvana/`. A `.migrated` sentinel prevents
  reruns.
- `core/settings.py`, `core/setup.py`, `core/user_hooks.py`,
  `core/skills.py`, `core/nirnamd.py`, `core/team.py`, and
  `mcp/config.py` now all resolve paths through `core/paths.py`.
- README, README.ko, and `install.sh` document the new layout and the
  `NERDVANA_DATA_HOME` / `NERDVANA_HOME` split.

#### Responsive sidebar (428 → 452 tests)

- New `nerdvana_cli/ui/sidebar.py` and `ui/sidebar_sections.py` add an
  opencode-style left sidebar with an auto-show breakpoint at 140 cols,
  35 cols fixed width, and `Ctrl+B` manual toggle with user-override
  semantics.
- Seven section widgets: session-topic+cwd header, provider/model +
  context-usage bar, collapsible Tools, MCP servers with connection
  status, collapsible Skills with count, migrated TaskPanel, and
  `CHANGES` powered by `git status --porcelain` polled every 2 s via
  `ui/git_status.py`.
- `ui/app.py` wraps the chat stack in `Horizontal`, captures session
  topic on first user prompt, and dispatches per-section refresh ticks.

#### Context injection overhaul (452 → 463 tests)

- `_environment_section` in `core/prompts.py` now emits platform, OS
  version, shell, `Is a git repository`, git branch, main branch, git
  status, and the last five commits. Git lookups use a 2 s subprocess
  timeout and fail silently.
- New `core/context_snapshot.py` runs once per session and collects
  project type (python/node/rust/go), project name, top-level
  directory tree, README headings, and entry points. Output is
  formatted into a `# Project Snapshot` block and appended to the
  sticky session context.
- New `core/context_reminder.py` owns a bounded ring buffer of the last
  five `RecentToolResult`s and builds a `<system-reminder>` block
  containing `turn=N`, `cwd`, and per-tool `name(args) [ok|err] preview`.
- `core/agent_loop.AgentLoop.run` injects the reminder as a synthetic
  user message before the real user prompt on every turn and records
  every `_execute_tools` outcome into the ring buffer.
- `activate_skill` / `deactivate_skill` split — the active skill body
  now persists across turns until `/clear` explicitly resets it (was
  previously cleared after one turn).

### Fixed

- `/model <id>` no longer re-detects the provider from the model name
  and no longer clobbers `base_url`. Selecting an Ollama Cloud model
  like `gemma4:31b-cloud` after `/provider ollama` used to silently
  fall back to Anthropic while keeping the Ollama `base_url`, routing
  Anthropic SDK traffic at Ollama's local HTTP server and returning
  `404 page not found`.
- `detect_provider` now recognises distinctively-Ollama naming (`:` tag
  suffix, `-cloud` suffix) before falling through to the Anthropic
  default.
- `/model <id>` selection is persisted to `~/.nerdvana/config.yml` so
  it survives a CLI restart. Unrelated keys (`api_key`, `session.*`)
  are preserved.
- Collapsible sidebar sections (Tools, MCP, Skills) now re-layout on
  toggle. Previously `self.refresh()` kept the cached `height: auto`
  measurement, so the arrow glyph flipped but the body stayed hidden.
  Fixed by passing `layout=True` to `refresh()`.

### Changed

- Baseline test count: 316 → 463 (+147).
- mypy strict still clean (pre-existing `types-pyperclip` stub
  warning unchanged).
- `ruff check nerdvana_cli/ ...` remains clean on every touched source
  file. Pre-existing `tests/` lint drift (52 rules across ~52 files)
  is tracked separately.

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

[Unreleased]: https://github.com/JinHo-von-Choi/nerdvana-cli/compare/v0.4.0...HEAD
[0.4.0]: https://github.com/JinHo-von-Choi/nerdvana-cli/releases/tag/v0.4.0
[0.3.0]: https://github.com/JinHo-von-Choi/nerdvana-cli/releases/tag/v0.3.0
