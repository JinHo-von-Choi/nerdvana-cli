# Lifecycle Hooks

The hook system in `nerdvana_cli/core/hooks.py` provides extension points that fire at well-defined moments inside the agent loop. Hooks let you observe, modify, or veto behaviour without patching the loop itself: they can inject additional messages, rewrite tool input, or short-circuit a tool call before it runs.

Hooks are registered against a `HookEvent` and receive a `HookContext` describing the current state. They return a `HookResult` (or `None` to opt out) that the engine consolidates into the loop.

## Events

`HookEvent` is a `StrEnum` with six members:

| Event | When it fires |
|-------|---------------|
| `SESSION_START` | Once when the agent loop is constructed, before the first user message is processed. |
| `SESSION_END` | Once when the loop terminates (normal completion or error path). |
| `BEFORE_TOOL` | Immediately before a tool is invoked. Handlers may rewrite `tool_input` or set `allow=False` to block the call. |
| `AFTER_TOOL` | Immediately after a tool has executed. Handlers receive the tool result and can inject follow-up messages. |
| `BEFORE_API_CALL` | Before each request to the model. |
| `AFTER_API_CALL` | After each model response. The `stop_reason` field carries `"max_tokens"`, `"end_turn"`, or `"tool_use"`. |

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
    modified_input: dict[str, Any] | None = None
```

- `allow=False` vetoes the current tool call (only meaningful for `BEFORE_TOOL`). The loop skips execution and propagates `message` to the model.
- `message` is a short, human-readable explanation. It is included in the synthetic tool error when a call is blocked.
- `inject_messages` is a list of fully-formed message dicts (`{"role": ..., "content": ...}`) that the loop appends to the conversation before the next API call. This is the canonical mechanism for context recovery and continuation prompts.
- `modified_input` (only for `BEFORE_TOOL`) replaces `tool_input` before the tool runs. Handlers can use this to sanitise paths, normalise arguments, or attach derived metadata.

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

Fires on `stop_reason == "end_turn"` and scans the most recent assistant message for residual `TODO`, `FIXME`, or `NotImplemented` markers. When it finds one, it injects a follow-up prompt that pushes the model to finish the work instead of declaring victory prematurely. This prevents the agent from halting on a half-finished implementation.

## User hook directories

NerdVana CLI auto-loads any `*.py` file from these directories on every
`AgentLoop` initialization:

| Path | Scope |
|------|-------|
| `~/.config/nerdvana-cli/hooks/` | Global — applies to every project |
| `<cwd>/.nerdvana/hooks/` | Project-local — checked in or per-project |

Files starting with `_` are skipped. Failures (import errors, missing
`register`, register raising) are logged and skipped — they never crash
the agent loop.

### Module contract

Each user hook module must export a module-level `register` function:

```python
from nerdvana_cli.core.hooks import HookEngine, HookEvent, HookContext, HookResult

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
from nerdvana_cli.core.hooks import HookContext, HookEvent, HookResult

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
