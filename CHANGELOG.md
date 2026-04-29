# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog 1.1.0](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning 2.0.0](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Moonshot AI (Kimi) provider: OpenAI-compatible API via `https://api.moonshot.ai/v1`. Default model: `kimi-k2-instruct`. API key: `MOONSHOT_API_KEY` or `KIMI_API_KEY`.
- Alibaba DashScope (Qwen Cloud) provider: OpenAI-compatible API via `https://dashscope-intl.aliyuncs.com/compatible-mode/v1`. Default model: `qwen3-coder-plus`. API key: `DASHSCOPE_API_KEY` or `ALIBABA_API_KEY`. Supports tools, streaming, vision, thinking. 1M context window.

### Changed

- Provider count updated from 15 to 17.

## [1.2.0] - 2026-04-29

### Added

- **IDE 패널 레이아웃**: Textual 기반 메인 앱에 프로젝트 트리(`project_tree.py`)와 에디터 패널(`editor_pane.py`)을 추가하여 단일 화면에서 파일 탐색·편집·대화가 가능한 IDE형 워크플로우 제공.
- **에디터 IO 분리**: 파일 열기/저장/디버운스 동작을 모듈화하여 멀티 패널 환경에서 안정적으로 동기화.
- **IDE 워크플로우 테스트**: `test_ide_layout`, `test_ide_workflow`, `test_editor_io`, `test_editor_pane`, `test_project_tree` 등 패널 동작 회귀 테스트 신규 추가.

### Changed

- **심볼 도구 모듈 분리**: `tools/symbol_tools.py`(897줄)에서 편집 책임을 `tools/symbol_edit_tools.py`(722줄)로 분리하여 단일 책임 원칙 준수 및 가독성 향상.
- **LSP 도구 확장**: `tools/lsp_tools.py`에 IDE 패널 연동에 필요한 신규 헬퍼 추가.
- **세션 코어 정리**: `core/session.py` 인터페이스를 멀티 패널 컨텍스트에 맞춰 미세 조정.

## [1.1.1] - 2026-04-24

### Added

- **Featherless AI provider**: OpenAI-compatible API via `https://api.featherless.ai/v1`. Default model: `featherless-llama-3-70b`. API key: `FEATHERLESS_API_KEY`. Note: standard endpoints do not support streaming.
- **Xiaomi MiMo provider**: OpenAI-compatible API via `https://token-plan-sgp.xiaomimimo.com/v1`. Default model: `mimo-v2.5-pro`. API key: `MIMO_API_KEY` or `XIAOMI_API_KEY`. Supports tools, streaming, vision, thinking. 1M context window.
- **Ollama self-hosted mode**: Setup wizard now supports three deployment modes: Local (default), Cloud (`https://ollama.com/v1`), Self-hosted (custom URL).
- **Context block splitting**: `split_into_blocks()` for topic-based conversation segmentation.
- **Block summarization**: `summarize_block()` and `compact_with_blocks()` for Memento-style context compression.
- **Memory importance tracking**: `MemoryEntry.importance` field (0.0-1.0), `min_importance` filter in `list_memories()`, `list_stale()` for time-based cleanup.
- **Agent context sharing**: `create_shared_context()` for summarized context sharing between agents.
- **Session restoration optimization**: `save_summary()`, `get_summary()`, `restore_with_summary()` for fast session recovery.

### Changed

- Provider count updated from 13 to 15.
- Ollama setup wizard enhanced with self-hosted URL input option.
- `nerdvana.yml.example` updated with new provider documentation.

## [1.1.0] - 2026-04-23

### Added

- Featherless AI and Xiaomi MiMo providers (same as 1.1.1 above).

## [1.0.0] - 2026-04-18

First stable release. The 0.9.x series shipped the full roadmap surface
area but a post-release audit uncovered five runtime-breaking defects in
the freshly landed server layer — the MCP server could not even boot,
authentication was defined but not wired, and Phase H tools were not
registered. 1.0.0 closes all five Critical issues, five Major drift
items, and two test-isolation bugs that were hiding real regressions
behind developer-machine pollution.

### Fixed

- **C-1** `nerdvana serve` boots. `rich.console.Console.print` does not
  accept a `file=` keyword; replaced with a dedicated
  `Console(stderr=True)` instance. Added a `serve` start-up regression
  test so the whole G1 → H server stack never silently breaks again.
- **C-2** `NerdvanaMcpServer._execute_tool` is now a real dispatcher:
  it builds a per-server tool map at init time, runs the BaseTool
  `parse_args → validate_input → call` chain, and serialises errors as
  `{"error": "..."}`. Previously the MCP server accepted tool calls
  but returned empty responses.
- **C-3** Authentication and ACL enforcement are wired into the request
  path. `_BearerAuthMiddleware` validates `Authorization: Bearer` for
  every HTTP request and parks the `AuthResult` in a `ContextVar`;
  stdio dispatch consults the uid check via `_verify_stdio_auth`;
  mTLS no longer fails open for unknown CNs. `client_identity` is
  now resolved per request instead of every caller being `anonymous`.
- **C-4** `auth.py` uses `hmac.compare_digest` for hash comparisons,
  closing a timing side-channel.
- **C-5** Phase H tools override the correct `check_permissions`
  signature (was `check_permission`, singular + wrong parameters) and
  return proper `PermissionResult(behavior=PermissionBehavior.*)`
  values. `ListQueryableProjects` is `ALLOW`; `RegisterExternalProject`
  and `QueryExternalProject` are `ASK` (filesystem write / subprocess
  spawn). Dead stubs deleted.
- **M-1** `create_tool_registry` registers the three external-project
  tools (`ListQueryableProjects`, `RegisterExternalProject`,
  `QueryExternalProject`). Gated by
  `settings.external_projects_enabled` so operators can disable the
  subprocess surface.
- **M-3** `BashTool` blacklist covers `$(…)`, `${…}`, and backtick
  substitutions, plus `eval`/`exec`, env-prefixed `sudo`, and
  `dd of=/dev/<block>`. A `_MAX_TIMEOUT = 600` ceiling is now enforced
  in `check_permissions`.
- **M-4** ASK permission has a concrete UX. TTY sessions prompt
  `Allow? [y/N]`; pipes / CI / `EOFError` default to `DENY`.
  Decisions are logged at `INFO`.
- **M-7** Removed the dead `state: LoopState` parameter that
  `ToolExecutor.run_batch` carried over from the Phase 0A split.
- **M-8** `audit.sqlite` is created atomically with
  `os.open(O_CREAT | O_EXCL, 0o600)`, eliminating the
  connect → chmod race. Both `AuditLogger` and `SanitizerAudit` go
  through the shared helper.
- **Test isolation** `tests/test_memories.py` and
  `tests/test_memory_tools.py` now monkeypatch
  `core_paths.global_memories_dir` to a tmp path so the developer's
  real `~/.nerdvana/memories/global/` contents never leak into the
  test run. Four pre-existing false failures disappear.

### Changed

- **M-5** `/help` output is generated from `ui.app.SLASH_COMMANDS`
  and the README REPL Slash Commands table is regenerated from the
  same list (21 entries). Drift prevention script checks it on every
  pre-commit.
- **M-6** Provider count confirmed at 13 (`ProviderName` enum
  includes ZAI/ZhipuAI). Lingering "12 platforms" copy fixed in
  `main.py` help text and README prose.
- **M-2** `AnchorMind` hook injection remains a placeholder for 1.0
  and is documented in `docs/adr/0003-anchormind-deferred.md`. The
  toggle stays `anchormind_inject: false`; setting it `true` returns
  an empty string with a clear log warning.
- **N-2** Production ruff violations cleared (F401 ×2, SIM105, UP017
  → 0). `contextlib.suppress(Exception)` replaces silent
  `try/except/pass`; `datetime.UTC` replaces `timezone.utc` in the
  `tool_executor` path.

### Added

- 130+ new tests across `tests/server/`, `tests/test_bash_*`,
  `tests/test_ask_permission_ux.py`,
  `tests/test_external_tools_*`,
  bringing the full suite to 1003 passed in `-m "not lsp_integration"`.

### Notes

- `docs/plans/2026-04-18-post-release-audit-plan.md` captures the
  full Hot/Next/Deferred triage that drove this release.
- The Deferred bucket (tests file lint, analytics connection pool,
  CI lsp_integration job, lazy-import formalisation, sanitizer ReDoS
  audit, retrospectives gitignore policy) is punted to post-1.0
  point releases.

## [0.9.2] - 2026-04-18

User-data preservation hardening for the self-update path.

### Added

- `core/updater.py` now enforces four guards before and after every
  `git pull` in the install tree:
  1. **Install/data separation** — refuses when `install_dir` equals
     or is nested under `user_data_home()` (or vice versa), so the
     pull can never target the user data root.
  2. **Dirty install refusal** — `git status --porcelain` must be
     empty; uncommitted changes in the install tree block the update
     with a clear message instead of being stashed or discarded.
  3. **Rotating pre-update snapshot** — user data is copied to
     `~/.nerdvana/.update-backups/pre-update-<timestamp>/` before the
     pull. The 3 most recent snapshots are retained. SQLite WAL/SHM
     sidecars are skipped to avoid copying a live database.
  4. **Post-pull integrity hash** — SHA-256 over `config.yml`,
     `NIRNA.md`, `mcp.json`, `mcp_acl.yml`, `mcp_keys.yml`,
     `external_projects.yml`, and every file under `contexts/`,
     `modes/`, `memories/`, `agents/`, `skills/`, `hooks/` is
     compared before and after the pull; any drift aborts the update
     and prints a restore command pointing at the snapshot.
- `core/migrate.run_if_needed()` is re-run after a successful update
  so schema migrations introduced by the new release take effect
  without requiring a second session.
- `tests/test_updater_preservation.py` — 13 tests covering
  separation guards, dirty refusal, snapshot rotation, integrity
  hashing, and an end-to-end no-op `run_self_update` run that
  preserves byte-identical user data.

### Changed

- `run_self_update` success message now includes the backup path
  (`Backup: ~/.nerdvana/.update-backups/pre-update-…`) and the count
  of pruned older snapshots.

## [0.9.1] - 2026-04-18

Debt cleanup release. Three tickets carried across Phase 0A → H are resolved
with no feature additions.

### Fixed

- `T-bug-ralph-loop`: `ralph_loop_check` hook now fires on `end_turn` stop
  reasons. `core/agent_loop.py` emits `AFTER_API_CALL` with
  `stop_reason="end_turn"` and carries the current assistant text in
  `HookContext.extra["asst_text"]` so the hook scans only the freshly
  returned message (no infinite re-injection across history). Parity
  scenario 8 snapshot updated to reflect the new correct behaviour —
  TODO markers now trigger the ralph continuation prompt.
- `T-debt-import-cycles`: all 8 baseline cycles were false positives from
  `scripts/check_import_graph.py` walking into `if TYPE_CHECKING:` guards
  and function-body lazy imports via `ast.walk`. The detector now uses a
  module-level walker that skips `TYPE_CHECKING` branches and function
  bodies. `.import_cycles_baseline.json` reduced to `count: 0`; `--strict`
  passes on the current 95-module graph.
- `T-debt-lsp-rename-symbol-tag`: `lsp_rename` now carries the `symbol`
  tag alongside `lsp` and `refactor`. It belongs in the same
  `filter(tags_all={"lsp","symbol"})` bucket as `lsp_goto_definition` and
  `lsp_find_references`; read/write separation remains enforced via
  `ToolCategory.WRITE` and `requires_confirmation=True`.

### Changed

- `core/loop_hooks.py`: stale comment noting the ralph-loop limitation
  removed, unused `type: ignore` cleaned up.
- `builtin_hooks.ralph_loop_check`: prefers `extra["asst_text"]` over
  history scan; returns early when empty.

## [0.9.0] - 2026-04-18

Phase H: External project subprocess isolation — natural-language queries to
external library/repository paths via isolated Python subprocesses + stdio MCP
channel.  Zero resource leaks, concurrency cap of 3, env-var token injection.

### Added

- `nerdvana_cli/core/external_projects.py` — `ExternalProject` model and
  thread-safe `ExternalProjectRegistry` with atomic YAML persistence
  (`~/.nerdvana/external_projects.yml`).
- `nerdvana_cli/server/external_worker.py` — `ExternalWorker` subprocess
  orchestrator: spawn/shutdown lifecycle, MCP JSON-RPC over stdio, SIGTERM →
  SIGKILL fallback, `max_concurrent=3` semaphore, env-var API-token injection.
- `nerdvana_cli/tools/external_project_tools.py` — three new tools:
  `ListQueryableProjects` (READ), `RegisterExternalProject` (WRITE, path safety
  validation), `QueryExternalProject` (READ+PROCESS, subprocess-isolated query).
- `tests/test_external_projects.py` — 13 registry CRUD + YAML round-trip tests.
- `tests/server/test_external_worker.py` — 8 spawn/shutdown/concurrency/timeout
  + env-injection tests.
- `tests/test_external_project_tools.py` — 12 tool schema + behaviour tests.
- `tests/server/test_external_integration.py` — 3 real-subprocess integration
  tests (`@pytest.mark.lsp_integration`).

### Changed

- `nerdvana serve` CLI — added `--project <path>` and `--mode <name>` flags
  (Phase H extension of Phase G1's `mcp_server.py`).  Existing behaviour
  unchanged when flags are omitted.
- `NerdvanaMcpServer.__init__` — added `project_path` and `mode` keyword args
  (server context for external-worker mode).

## [0.8.5] - 2026-04-18

Phase G3+G4: Textual TUI observability dashboard + analytics + cost tracking.

### Added

- `nerdvana_cli/core/token_estimator.py` — `TokenEstimator` ABC with
  `TiktokenEstimator` (OpenAI), `AnthropicExactEstimator` (count_tokens API),
  and `CharEstimator` (fallback). `TokenEstimatorRegistry.get_for(provider)`
  auto-selects the best estimator.
- `nerdvana_cli/core/analytics.py` — `AnalyticsWriter` records tool calls +
  sessions into `~/.nerdvana/analytics.sqlite` (WAL mode, separate from
  audit.sqlite). `AnalyticsReader` provides query helpers for /health and
  dashboard. `PricingTable` loads pricing.yml and estimates USD cost.
- `nerdvana_cli/providers/pricing.yml` — provider/model USD pricing table for
  13 providers (Anthropic, OpenAI, Google, Groq, Mistral, DeepSeek, Fireworks,
  Cohere, Together, Ollama, vLLM, LM Studio).
- `nerdvana_cli/ui/dashboard_tab.py` — `DashboardTab` Textual widget: session
  header, tool heatmap, failure rate panel, live log tail, token sparkline,
  health footer. Toggle via `Ctrl+D` or `/dashboard`.
- `nerdvana_cli/commands/observability_commands.py` — `/health [--days N]
  [--json]` and `/dashboard` slash command handlers.
- `ToolExecutor` analytics hook: timing and success/failure recorded per tool
  call when `analytics_writer` is provided (opt-in, no impact on existing code).
- `/tokens` extended with accumulated session cost from analytics DB.

### Changed

- `pyproject.toml`: version 0.8.0 → 0.8.5; `tiktoken>=0.7.0` added to
  `[dev]` optional dependencies.
- `app.py`: `Ctrl+D` binding for dashboard toggle; `/dashboard` and `/health`
  slash commands; `DuplicateID` fix for skill triggers that clash with built-in
  commands.

## [0.8.3] - 2026-04-18

Phase G2: External harness hook bridge + dual-gate sanitizer.

### Added

- `nerdvana_cli/server/hook_schemas.py` — TypedDict models for Claude Code /
  Codex / VSCode hook payloads; `make_response` factory for `HookResponse`.
- `nerdvana_cli/server/sanitizer.py` — dual-gate sanitiser (v3.1 §3.3):
  Gate-1 blacklist tags 7 prompt-injection patterns; Gate-2 rejects
  `role:system` / `<system>` / tool-definition structures.  Sensitive-data
  redaction (OpenAI key, AWS AKID, email, bare tokens).  4 096-char cap.
  `SanitizerAudit` records all events to `sanitizer_events` table.
- `nerdvana_cli/server/hook_bridge.py` — full `HookBridge` implementation;
  handles `pre-tool-use` (approve + optional context), `post-tool-use`
  (context from tool output), `prompt-submit` (AnchorMind placeholder, opt-in).
  `run_hook` stdin→stdout helper.  All calls logged to `hooks` table.
- `nerdvana_cli/server/audit.py` — added `hooks` and `sanitizer_events` DDL
  (ADD-only; no existing tables removed).
- `main.py` — `nerdvana hook {pre-tool-use,post-tool-use,prompt-submit,list}`
  sub-command group.
- `tests/server/test_sanitizer.py` (16), `tests/server/test_hook_bridge.py`
  (11), `tests/server/test_hook_audit.py` (3),
  `tests/server/test_hook_bridge_integration.py` (3) — +33 tests (749 → 782).

### Security

- Gate-2 structure rejection prevents system-prompt injection via hook context.
- Sensitive-data redaction applied to all injected context before dispatch.

## [0.8.0] - 2026-04-18

Phase G1: MCP server mode — external harnesses can call nerdvana tools over
MCP 1.0 (stdio + HTTP JSON-RPC) with API-key authentication, Unix-socket UID
check, mTLS peer-CN auth, role-based ACL, and SQLite WAL audit logging.

### Added

- `nerdvana_cli/server/__init__.py` — server package root.
- `nerdvana_cli/server/mcp_server.py` — `NerdvanaMcpServer` wrapping FastMCP;
  6 read-only tools always available, 9 write tools behind `--allow-write` +
  `confirm: true` dual-gate (v3 §7.5).
- `nerdvana_cli/server/auth.py` — `AuthManager`: HTTP Bearer sha256-hash match
  vs `~/.nerdvana/mcp_keys.yml`, Unix socket 0600/UID check, mTLS peer-CN
  authentication.
- `nerdvana_cli/server/acl.py` — `ACLManager`: role→tool mapping loaded from
  `~/.nerdvana/mcp_acl.yml`; unknown clients auto-assigned `read-only` (v3.1 §3.1).
- `nerdvana_cli/server/audit.py` — `AuditLogger`: SQLite WAL audit table, 0600
  file permissions, pruning to keep file < 1 MB after every 1 000 writes (v3.1 §1.4).
- `nerdvana_cli/server/hook_bridge.py` — Phase G2 scaffold: stdin JSON reader +
  `HookBridge.dispatch` stub.
- `main.py` — `nerdvana serve` command (`--transport {stdio,http}`,
  `--port`, `--host`, `--allow-write`, `--tls-cert`, `--tls-ca`).
- `main.py` — `nerdvana admin acl {list,revoke,add}` sub-commands.
- `tests/server/test_mcp_server.py` (11 tests), `tests/server/test_auth.py`
  (7 tests), `tests/server/test_acl.py` (7 tests), `tests/server/test_audit.py`
  (4 tests) — total +30 tests (719 → 749 tests collected).

### Security

- API keys stored as `sha256:<hex>` only — no plaintext in YAML.
- Default bind: `127.0.0.1`; `--host 0.0.0.0` prints a warning to stderr.
- Audit log file created with permissions 0600 (owner read-write only).

## [0.7.0] - 2026-04-18

Phase F: Runtime Profiles — context × mode × trust_level.

Two-axis YAML profile system: context profiles describe the harness environment
(standalone, claude-code, vscode, ide, codex) and mode profiles describe the
current task type (planning, editing, interactive, one-shot, query, architect,
no-onboarding, no-memories). Both axes combine to produce a merged tool-visibility
filter, prompt injection, and trust level for every agent turn.

### Added

- `core/profiles.py` — `ContextProfile`, `ModeProfile`, `MergedProfile`
  dataclasses + `ProfileManager` (context × mode synthesis, mode stack,
  `visible_tools()` filter, project/user/builtin YAML resolution).
- `resources/profiles/contexts/` — 5 built-in context YAMLs:
  `standalone`, `claude-code`, `codex`, `vscode`, `ide`.
- `resources/profiles/modes/` — 8 built-in mode YAMLs with `trust_level`:
  `interactive` (balanced), `editing` (balanced), `planning` (strict),
  `query` (strict), `architect` (strict, model_override=claude-opus-4-7),
  `one-shot` (yolo), `no-onboarding` (balanced), `no-memories` (balanced).
- `commands/profile_commands.py` — `/mode` and `/context` slash-command
  handlers (list / activate / deactivate / status).
- `tools/profile_tools.py` — `GetCurrentConfigTool`, `ActivateModeTool`,
  `DeactivateModeTool` (agent-accessible profile inspection and mode control).
- `core/settings.py` — `SessionConfig.default_context` + `default_mode`
  fields; `planning_gate=true` auto-maps to `default_mode=planning` (deprecated
  in 0.8.0).
- `core/paths.py` — `user_contexts_dir`, `user_modes_dir`,
  `project_contexts_dir`, `project_modes_dir` path helpers.
- `main.py` — `--approval-mode {default,auto_edit,yolo,plan}` CLI flag with
  Codex-style mapping to (mode, trust_level).
- `ui/app.py` — `/mode` and `/context` routed in `_handle_command`;
  both entries added to `SLASH_COMMANDS` popup.
- `tests/test_profiles.py` (32 tests), `tests/test_mode_commands.py` (10 tests),
  `tests/test_approval_mode.py` (8 tests) — total +50 tests.

### Changed

- `commands/system_commands.py` — `/help` output lists `/mode` and `/context`.

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
