# Headless Runs

`nerdvana run` runs one prompt and exits. Scripts, CI jobs and programs that embed the agent use it.

```bash
nerdvana run "fix the failing test" --approval-mode yolo --max-turns 30 --max-cost-usd 2 --output-format json
```

## Options

| Option | Meaning |
|-|-|
| `--output-format text\|json\|stream-json` | `text` (default) streams for a person to read. `json` prints one result object at the end. `stream-json` prints one JSON event per line and ends with the same result object. In both JSON formats stdout carries only JSON; notices go to stderr. |
| `--max-turns N` | Stop after N model turns. |
| `--max-cost-usd X` | Stop once the estimated cost of the run reaches X USD (needs a known price for the model). |
| `--max-total-tokens N` | Stop once the input and output tokens of all requests reach N. Needs no price, so it works for any model. |
| `--require-price` | Refuse to run when `--max-cost-usd` is set but the model has no known price. |
| `--image PATH` | Attach an image (PNG, JPEG, GIF or WebP, up to 5 MB, at most 6) to the prompt (repeatable). The type is read from the file's first bytes. |
| `--set section.field=value` | Override one setting for this run (repeatable; the value is read as YAML). The `permissions`, `hooks` and `sandbox` sections cannot be changed this way. |
| `--resume SESSION_ID` | Continue the recorded conversation of that session (see `nerdvana session list`); the prompt is the next message. |
| `--verify COMMAND` | The command that decides whether the task is done. When the model says it is finished the command runs; a non-zero exit sends the end of its output back to the model, which keeps working. The run ends when it passes, after `--verify-attempts N` failures (default `goal.max_attempts`, 5) or at a turn or cost limit. |
| `--scope PATH` | With `--verify`: paths the task is about (repeatable). An edit outside them asks first, and is refused when nobody can be asked. |
| `--sandbox off\|auto\|require` | Confine what shell commands can write for this run, overriding `sandbox.mode` (see [sandbox.md](sandbox.md)). |
| `--approval-mode default\|auto_edit\|yolo\|plan` | Permission preset. Without a terminal a confirmation is refused, so unattended runs that write files usually need `yolo`. |
| `--json-schema SCHEMA` | A JSON schema, as a file path or inline JSON. The final answer must be JSON that fits it (a ```` ```json ```` fence is allowed); a misfit is sent back to the model with the reason, up to two times. The parsed value is the result's `structured_output`; an answer that never fits ends the run with `error_schema` (exit code 1). |
| `--input-format text\|stream-json` | With `stream-json`, after the prompt every line of standard input is one more user message, run in the same session as it arrives: `{"type": "user", "message": {"content": "..."}}`, `{"text": "..."}` or `{"prompt": "..."}`. Give `-` as the prompt to take every message from standard input. |
| `--diagnostics-dir DIR` | A new or empty directory that receives `events.jsonl` (every event, whatever the output format shows), `settings.yml` (the settings used, credentials masked) and `result.json`. |

## Exit codes

| Code | Meaning |
|-|-|
| `0` | Success |
| `1` | The run failed (provider error, unexpected error) |
| `2` | Invalid options or configuration, a missing API key included |
| `3` | A turn, cost, token or verification limit stopped the run |

## Result object

`schema_version` is 1. Fields are only ever added.

```json
{"type": "result", "schema_version": 1, "subtype": "success", "is_error": false,
 "result": "final answer text", "session_id": "ab12cd34", "provider": "anthropic",
 "model": "claude-sonnet-5-5", "num_turns": 4, "duration_ms": 18234, "total_cost_usd": 0.0421,
 "usage": {"input_tokens": 51230, "output_tokens": 2210, "cache_read_tokens": 38000, "cache_write_tokens": 9000},
 "signals": {"cas_rejected": 1, "new_diagnostics": 2}}
```

| Field | Content |
|-|-|
| `subtype` | `success`, `error_max_turns`, `error_max_cost`, `error_max_total_tokens`, `error_goal_unmet`, `error_unpriced`, `error_max_tokens`, `error_provider`, `error_during_run`, `error_config` or `error_schema`. An error result also has an `error` string when one is known. |
| `result` | The text the model wrote after its last tool call. |
| `structured_output` | With `--json-schema`: the final answer parsed as JSON and checked against the schema. |
| `price_known` | `false` when the model has no known price: `total_cost_usd` is then 0 because the cost is unknown, not because the run was free. |
| `signals` | Counts of what went wrong during the run, by kind: `cas_rejected`, `repeat_refused`, `new_diagnostics`, `invalid_input`, `permission_denied_user`, `sandbox_denied`, `tool_error`, `todo_nudge`, `provider_retry`, `provider_fallback`, `compaction`, `observations_masked`, `no_progress`, `untrusted_source`, `cache_miss` and a few more. A kind that did not occur is absent. |
| `verification` | Present with `--verify`: the outcome of the verification command. |
| `receipt` | Present when the run changed files or had a `--verify` goal (`receipt_version` 1). It holds `files_changed` (applied edits per file), `verification`, the `sandbox` policy, `cost_by_agent` (requests and USD per agent type, sub-agents included) and `problems` (stale-file edit refusals, new language-server errors, refused repeats, out-of-scope edits, masked secrets, failed verifications, escalations). It is assembled from what the run measured; the model writes none of it. |

## `stream-json` events

One JSON object per line, before the result object.

| Event | Content |
|-|-|
| `system` | Subtype `init`, with the session id, provider and model |
| `text` | A piece of the answer |
| `notice` | A message from the agent itself, such as a retry or a fallback |
| `tool_start` | `name`, `summary` |
| `tool_done` | `name`, `is_error`, `result` (the first line of the tool's result) |
| `request` | One provider request: `provider`, `model`, `agent_type`, `turn`, `last_tool`, the token counts including `cache_read_tokens` and `cache_write_tokens`, `cost_usd`, `first_event_ms` (time to the first streamed event) and `request_ms` (time to the end of the response) |
| `compaction` | The history was compacted |
| `context` | Percent of the context window in use |
