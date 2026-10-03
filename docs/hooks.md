# Lifecycle Hooks

The hook system in `nerdvana_cli/core/hooks/hooks.py` provides extension points that fire at well-defined moments inside the agent loop. Hooks let you observe, modify, or veto behaviour without patching the loop itself: they can inject additional messages, rewrite tool input, or short-circuit a tool call before it runs.

Hooks are registered against a `HookEvent` and receive a `HookContext` describing the current state. They return a `HookResult` (or `None` to opt out) that the engine consolidates into the loop.

## Events

`HookEvent` is a `StrEnum` with twelve members:

| Event | When it fires |
|-------|---------------|
| `SESSION_START` | Once per session, on the first prompt (and again on the first prompt after `/clear`). |
| `SESSION_END` | Once per session that started: when the TUI exits, when a `nerdvana run` finishes or fails, and on `/clear` before the history is reset. `extra` carries `reason` (`exit` or `reset`) and `session_id`. |
| `BEFORE_TOOL` | Immediately before a tool is invoked. Handlers may rewrite `tool_input` or set `allow=False` to block the call. |
| `AFTER_TOOL` | Immediately after a tool has executed. Handlers receive the tool result; messages they inject are appended after the batch's tool results, never between a call and its result. |
| `BEFORE_API_CALL` | Before each request to the model. |
| `AFTER_API_CALL` | After each model response. The `stop_reason` field carries `"max_tokens"`, `"end_turn"`, or `"tool_use"`. |
| `PERMISSION_DENIED` | A tool call was refused: by the permission policy (`always_deny`, a mode that hides the tool, a tool's own refusal), by the user answering no, or by the action classifier. `tool_name` and `tool_input` name the call. A handler may return a `message`: it is appended to the refusal the model reads as `[Retry hint from a hook: ...]`. Refusals made by the executor itself (a malformed input, a repeated call, `BEFORE_TOOL` vetoes, edit scopes) do not fire it. |
| `PRE_COMPACT` | Before the conversation is compacted, whether the window filled up or the provider asked for it. A handler that returns `allow=False` cancels this compaction; the `message` is the reason, which is logged and written to the session transcript, and the history is left as it is. |
| `POST_COMPACT` | After a compaction finished. Cannot change anything. |
| `PRE_MODEL_SWITCH` | Just before the model in use changes. Observation only. |
| `POST_MODEL_SWITCH` | Just after the model in use changed. Observation only. |
| `INSTRUCTIONS_LOADED` | Once per session, at its first prompt, with the project instruction documents (`NIRNA.md`, `AGENTS.md`, `CLAUDE.md` and the user's own) the system prompt carries. Rule files injected later when a file in a subdirectory is first touched do not fire it. Observation only. |

The payload of the last five and the extra fields of `PERMISSION_DENIED` are in `HookContext.extra`:

| Event | `extra` keys |
|-------|--------------|
| `PERMISSION_DENIED` | `source` (`policy`, `user` or `classifier`), `reason` (the refusal text before any hint) |
| `PRE_COMPACT` | `tokens` (estimated context size), `messages` (message count) |
| `POST_COMPACT` | `tokens_before`, `messages_before`, `messages_after`, `strategy` (`ai`, or `naive` when the model summary failed or its circuit breaker is open) |
| `PRE_MODEL_SWITCH`, `POST_MODEL_SWITCH` | `from_provider`, `from_model`, `to_provider`, `to_model`, `reason` (`escalation` for `session.escalation_model`, `fallback` after a failed request, `restore` when a prompt ends and the loop goes back to the model it started on) |
| `INSTRUCTIONS_LOADED` | `files`: a list of `{path, type, chars}` (`type` is `global`, `project` or `local`) |

There is no `ConfigChange` event: settings are changed in several places (the model and provider pickers, `/thinking`, `/activity` and others) that write the configuration file themselves, with no single point that could report a change to the hook engine.

## HookContext

Every hook handler receives a `HookContext` instance:

```python
@dataclass
class HookContext:
    event: HookEvent
    settings: Any = None
    tools: list[Any] = field(default_factory=list)
    messages: list[Any] = field(default_factory=list)
    tool_name: str = ""
    tool_input: dict[str, Any] = field(default_factory=dict)
    tool_result: Any = None
    stop_reason: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)
```

Field semantics:

- `event`: the `HookEvent` that triggered the handler.
- `settings`: the live `AgentSettings` object (model, system prompt, limits).
- `tools`: the registered tool instances available to the loop.
- `messages`: the running message history. Mutating this list directly is discouraged; prefer `HookResult.inject_messages`.
- `tool_name`: populated for `BEFORE_TOOL` and `AFTER_TOOL`. Empty for other events.
- `tool_input`: the validated input dict for the current tool call.
- `tool_result`: the tool's return value or error payload (only set for `AFTER_TOOL`).
- `stop_reason`: the model's stop reason for `AFTER_API_CALL`.
- `extra`: a free-form bag the loop uses to communicate auxiliary state. For example, the JSON parser sets `extra["json_error"]` when tool input cannot be decoded.

A `HookContext` is constructed fresh for every dispatch, so handlers should treat it as ephemeral and avoid retaining references across invocations.

## HookResult

Handlers return a `HookResult` (or `None`):

```python
@dataclass
class HookResult:
    allow: bool = True
    message: str = ""
    inject_messages: list[dict[str, Any]] = field(default_factory=list)
    system_prompt_append: str = ""
```

- `allow=False` vetoes the current tool call (`BEFORE_TOOL`) or the compaction about to run (`PRE_COMPACT`). A vetoed tool call is not executed and `message` goes to the model; a vetoed compaction is skipped and `message` is logged. On any other event `allow` is ignored.
- `message` is a short, human-readable explanation. It is included in the synthetic tool error when a call is blocked, and on `PERMISSION_DENIED` it is the retry hint appended to the refusal.
- `inject_messages` is a list of fully-formed message dicts (`{"role": ..., "content": ...}`) that the loop appends to the conversation before the next API call. This is the canonical mechanism for context recovery and continuation prompts.
- `system_prompt_append` is exclusive to `SESSION_START` hooks. The returned string is cumulatively appended to the system prompt on every turn, persisting for the model across the entire session.

Returning `None` is equivalent to returning a default `HookResult()` and is the right choice when a handler is purely observational.

## Registration

Hooks live inside a `HookEngine` instance owned by the agent loop:

```python
engine.register(HookEvent.AFTER_API_CALL, my_handler)
engine.unregister(HookEvent.AFTER_API_CALL, my_handler)
```

`HookEngine.fire(context)` invokes every handler bound to `context.event` in registration order, swallows handler exceptions (logged at `WARNING`), and returns the list of non-`None` `HookResult` values for the loop to merge.

## Built-in handlers

`AgentLoop.__init__` auto-registers four hooks that implement the loop's recovery and quality-control behaviours.

### session_start_context_injection (`SESSION_START`)

Runs once at startup and seeds the conversation with the operating context. It injects:

- the registered tool list (names and short descriptions),
- the contents of `NIRNA.md` from the working directory, when present,
- a compact settings summary (model, max tokens, system prompt fingerprint).

This hook is what makes the rest of the session aware of project conventions without forcing the user to paste them manually.

### context_limit_recovery (`AFTER_API_CALL`)

Watches the model's `stop_reason`. When it sees `"max_tokens"`, the handler injects a continuation message instructing the model to resume from the truncation point. The injection lands in `inject_messages` so the next API call carries the recovery prompt without rewriting history.

### json_parse_recovery (`AFTER_TOOL`)

Triggered when the loop sets `context.extra["json_error"]` after a tool call whose arguments could not be parsed. The handler injects a correction message describing the parser failure so the model can retry with valid JSON. Without this hook, malformed tool calls would either crash the loop or be silently dropped.

### ralph_loop_check (`AFTER_API_CALL`)

Fires on `stop_reason == "end_turn"` and scans the most recent assistant message for residual `TODO`, `FIXME`, or `NotImplemented` markers. When it finds one, it injects a follow-up prompt that pushes the model to finish the work instead of declaring victory prematurely. This prevents the agent from halting on a half-finished implementation. End-of-turn hooks can continue a turn at most three times per prompt, so a reply that keeps mentioning `TODO` cannot hold the loop.

### directory rules (`AFTER_TOOL`)

When `FileRead`, `FileEdit` or `FileWrite` first touches a file in a subdirectory, the `NIRNA.md`, `AGENTS.md` and `CLAUDE.md` files between that directory and the project root are injected once, nearest first, up to 32 KB per session.

## Command hooks

Shell commands can be declared without writing Python, in `~/.nerdvana/hooks.yml` and `<project>/.nerdvana/hooks.yml`; see "Custom Commands and Command Hooks" in the README for the format. Each entry names an event (`before_tool`, `after_tool`, `session_start`, `session_end`, `permission_denied`, `pre_compact`, `post_compact`, `pre_model_switch`, `post_model_switch` or `instructions_loaded`), an optional `match` glob on the tool name (the tool events `before_tool`, `after_tool` and `permission_denied` only), a `command` and a `timeout`.

The command receives one JSON object on standard input: `event`, `cwd`, then `tool_name` and `tool_input` for the three tool events, a shortened `tool_result` for `after_tool`, and `details` (the `extra` keys of the table above) for `permission_denied`, `pre_compact`, `post_compact`, `pre_model_switch`, `post_model_switch` and `instructions_loaded`.

An exit code of 2 means:

| Event | Effect of exit code 2 |
|-------|-----------------------|
| `before_tool` | Vetoes the call (`HookResult(allow=False)`), the command's output is the message. |
| `after_tool` | Injects the command's output as a user message. |
| `permission_denied` | The command's output is appended to the refusal as a retry hint. |
| `pre_compact` | Cancels this compaction, the output is the reason. |
| every other event | Ignored. |

Exit code 0 carries on, and every other outcome (another exit code, a timeout, a command that cannot start) is logged and ignored. A project `hooks.yml` is subject to the same opt-in and digest approval as project Python hooks.

```yaml
hooks:
  - event: permission_denied
    match: "Bash"
    command: "scripts/explain-denial.sh"   # prints a sentence on stdout and exits 2: the model reads it next to the refusal
```

## User hook directories

NerdVana CLI auto-loads any `*.py` file from these directories on every
`AgentLoop` initialization:

| Path | Scope | Runs by default |
|------|-------|-----------------|
| `~/.nerdvana/hooks/` | Global, applies to every project | Yes |
| `<cwd>/.nerdvana/hooks/` | Project-local | No, see below |

Files starting with `_` are skipped. Failures (import errors, missing
`register`, register raising) are logged and skipped — they never crash
the agent loop.

### Project-local hooks are opt-in and per-file approved

A project hook is arbitrary Python carried by a repository, so cloning an
untrusted repository must not be enough to execute it. Both conditions have
to hold before one runs:

1. `hooks.allow_project_hooks: true` in the active config file. Without it
   every file under `<cwd>/.nerdvana/hooks/` is skipped with a log line
   naming the setting.
2. The file's SHA-256 digest matches the digest recorded for that exact
   absolute path in `~/.nerdvana/trusted_hooks.json`. A hook with no record,
   or one whose bytes changed since approval, is skipped as unapproved.

Approval is bound to the bytes present at the moment it is granted, so any
later edit, yours or a `git pull`'s, revokes it until it is granted again.

```console
$ nerdvana hook trust .nerdvana/hooks/my_hook.py
$ nerdvana hook trusted
$ nerdvana hook revoke .nerdvana/hooks/my_hook.py
```

`nerdvana hook trusted` lists every approval and marks the ones whose file
changed or disappeared since it was granted. Deleting
`~/.nerdvana/trusted_hooks.json` drops all of them at once. The same
operations are available as `trust_project_hook` and `revoke_project_hook`
in `nerdvana_cli.core.hooks.user_hooks`.

Global hooks live under your own data directory and are subject to neither
condition.

### Module contract

Each user hook module must export a module-level `register` function:

```python
from nerdvana_cli.core.hooks.hooks import HookEngine, HookEvent, HookContext, HookResult

def register(engine: HookEngine, settings) -> None:
    """Called once per AgentLoop init. Register any number of handlers."""
    engine.register(HookEvent.SESSION_START, _my_handler)

def _my_handler(ctx: HookContext) -> HookResult:
    return HookResult(system_prompt_append="Custom guidance for the model.")
```

### `system_prompt_append` vs `inject_messages`

- `HookResult.system_prompt_append` — sticky text appended to the system
  prompt on every turn until `reset_session()` is called.
- `HookResult.inject_messages` — one-shot conversation messages prepended
  to the next turn only.

## Writing your own hook

A minimal observational hook:

```python
from nerdvana_cli.core.hooks.hooks import HookContext, HookEvent, HookResult

def log_tool_calls(ctx: HookContext) -> HookResult | None:
    if ctx.event is HookEvent.BEFORE_TOOL:
        logger.info("calling %s with %s", ctx.tool_name, ctx.tool_input)
    return None

agent.engine.register(HookEvent.BEFORE_TOOL, log_tool_calls)
```

A guarding hook that blocks dangerous shell commands:

```python
def block_rm_rf(ctx: HookContext) -> HookResult | None:
    if ctx.tool_name != "shell":
        return None
    cmd = ctx.tool_input.get("command", "")
    if "rm -rf /" in cmd:
        return HookResult(allow=False, message="refused: destructive command")
    return None
```

A recovery hook that reacts to tool failures:

```python
def retry_on_network_error(ctx: HookContext) -> HookResult | None:
    if ctx.event is not HookEvent.AFTER_TOOL:
        return None
    if not isinstance(ctx.tool_result, dict):
        return None
    if ctx.tool_result.get("error_type") != "network":
        return None
    return HookResult(
        inject_messages=[
            {
                "role": "user",
                "content": "The previous tool failed with a transient network error. Retry once.",
            }
        ]
    )
```

## Design notes

- Handlers must be fast. They run on the hot path of the loop and any blocking I/O delays the next API call.
- Handlers should be idempotent. Recovery hooks in particular may be invoked repeatedly within a single session.
- Exceptions raised inside a handler are caught by `HookEngine.fire` and logged, but the corresponding `HookResult` is dropped. Defensive code is preferable to relying on the engine's safety net.
- Use `extra` for cross-handler coordination instead of mutating `messages` or `tool_input` outside of the documented `HookResult` channel.

## Built-in recovery hooks

Three recovery hooks are defined in `nerdvana_cli/core/builtin_hooks.py` and registered automatically inside `AgentLoop.__init__`. Users do not need to register them; they are always active.

- **`context_limit_recovery`** — registered on `AFTER_API_CALL`: triggers when `ctx.stop_reason == "max_tokens"`; injects a continuation message asking the model to resume from where it left off, optionally including the last user message (up to 200 characters) as context.
- **`json_parse_recovery`** — registered on `AFTER_TOOL`: triggers when `ctx.extra["json_error"]` is populated by the loop after a tool call whose arguments could not be parsed; injects a correction message containing the tool name and parse error, instructing the model to retry with valid JSON.
- **`ralph_loop_check`** — registered on `AFTER_API_CALL`: triggers when `ctx.stop_reason == "end_turn"` and the current assistant turn (read from `ctx.extra["asst_text"]`, falling back to message history) contains any of the patterns `TODO`, `FIXME`, `#\s*구현\s*필요`, `#\s*미구현`, `#\s*needs?\s*implementation`, `NotImplemented`, or `raise\s+NotImplementedError`; injects a follow-up message listing up to five unique matched markers and asking the model to complete all unfinished items before declaring the task done.
