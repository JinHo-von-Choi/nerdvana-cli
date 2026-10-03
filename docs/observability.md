# OpenTelemetry traces

nerdvana can export OpenTelemetry traces of what an agent does: each run, each model
request with its token usage, and each tool call. It is optional and off by default.
With it off the OpenTelemetry packages are never imported.

## Turning it on

```
pip install 'nerdvana-cli[otel]'
```

```yaml
telemetry:
  otel:
    enabled: true
    endpoint: http://localhost:4318   # or set OTEL_EXPORTER_OTLP_ENDPOINT
```

The exporter speaks OTLP over HTTP. `endpoint` is the collector's base URL and
`/v1/traces` is appended; left empty, the standard `OTEL_EXPORTER_OTLP_ENDPOINT`
variable is used, and `OTEL_EXPORTER_OTLP_HEADERS` and the other `OTEL_EXPORTER_OTLP_*`
variables apply either way. All options are in [configuration.md](configuration.md#telemetryotel-otelconfig).

When `enabled` is true and the SDK is missing, startup prints one line saying so and
tracing stays off. A collector that is down or an export that fails never affects a run;
the spans are dropped and a warning is logged.

## What is recorded

| Span | Kind | One per | Attributes |
|-|-|-|-|
| `invoke_agent {agent}` | internal | run of a prompt, by the main agent or a sub-agent | `gen_ai.operation.name`, `gen_ai.agent.name`, `gen_ai.agent.id`, `gen_ai.provider.name`, `gen_ai.request.model`, `gen_ai.conversation.id` |
| `chat {model}` | client | provider request | `gen_ai.operation.name`, `gen_ai.provider.name`, `gen_ai.request.model`, `gen_ai.conversation.id`, `gen_ai.usage.input_tokens`, `gen_ai.usage.output_tokens`, `gen_ai.usage.cache_read.input_tokens`, `gen_ai.usage.cache_write.input_tokens`, `nerdvana.cost_usd`, `nerdvana.turn` |
| `execute_tool {tool}` | internal | tool call | `gen_ai.operation.name`, `gen_ai.tool.name`, `gen_ai.tool.type`, `gen_ai.tool.call.id`, `gen_ai.conversation.id`, `error.type` when the call failed |

`gen_ai.conversation.id` is the session id. The `chat` and `execute_tool` spans are
children of the `invoke_agent` span of the loop that made them. A sub-agent's
`invoke_agent` span is a child of the `execute_tool Agent` (or `Swarm`) span of the session
that started it, so one trace shows the whole tree. `gen_ai.usage.input_tokens` is the
whole prompt, cached part included.

The names of the `gen_ai.*` attributes follow the OpenTelemetry GenAI semantic
conventions, which are all at the Development stability level and may still be renamed.
They are kept in one table, `nerdvana_cli/core/telemetry/otel_semconv.py`, so a rename touches
one file.

A tool call that was refused before it ran (a permission denial, a hook veto, a failed
validation) appears as an `execute_tool` span of zero length marked `error.type=tool_error`.
Tool calls run together (read-only tools) all end when the last of them finishes.

## Content

With `capture_content: true` the spans also carry `gen_ai.input.messages` (the history
sent with each request, as JSON roles and typed parts), `gen_ai.tool.call.arguments` and
`gen_ai.tool.call.result`. Everything passes through the same secret masker as tool
output (see [secret-masking.md](secret-masking.md)) before it is attached, and each
attribute is cut at 32768 characters. Masking is a mitigation, not a boundary: prompts,
code and command output remain sensitive, so send content only to a collector you
control. It is off by default.

## Child processes

While a tool call is running, the `Bash` tool passes the W3C trace context of its
`execute_tool` span to the command as `TRACEPARENT` (and `TRACESTATE`), so a command
that is itself instrumented continues the trace. Without tracing the environment is
unchanged.

## MCP

The `traceparent` of a tool call is not yet sent to MCP servers: the MCP client sends
`tools/call` without a place for the request's `_meta` field. The `execute_tool` span of
an MCP tool is recorded like any other.

## Wiring

`setup(settings)` in `nerdvana_cli/core/telemetry/telemetry_otel.py` starts tracing. It is called
once from `resolve_run_provider` in `cli/runtime.py`, the step the interactive start,
`nerdvana run` and `nerdvana review` all take before building an agent.
`register_activity_hooks` calls `observe_loop` for every agent loop, so sub-agents are
covered without a call in their code. The observer listens to the `before_api_call`,
`after_api_call`, `before_tool`, `after_tool` and `session_end` hook events and to the
loop's usage listener; `nerdvana run` chains its own usage reporter behind it.
