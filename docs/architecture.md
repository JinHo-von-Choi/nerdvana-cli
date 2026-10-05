# Architecture

The package is `nerdvana_cli`. Imports point down the layers below; the structure tests in
`tests/contracts` fail when one points up.

## Packages

| Package | Owns | May import |
|-|-|-|
| `types`, `utils` | message and tool result types, path and schema helpers | nothing else in the package |
| `providers` | the provider adapters, model metadata and pricing | `types`, `utils` |
| `codeintel` | the language server client, symbol resolution and symbol-level editing | `utils` |
| `agents` | built-in and user agent definitions | nothing else in the package |
| `core` | the agent and everything it runs on (subpackages below) | `providers`, `agents`, `types`, `utils` |
| `external` | registered external projects and the worker that runs commands in them | `core` and below |
| `mcp` | the MCP client: servers, transports, deferred tool loading | `core` and below |
| `tools` | the tools the model calls | `core`, `codeintel`, `external`, `mcp` and below |
| `server` | `nerdvana serve`, the MCP server for other agents | `tools` and below |
| `acp` | `nerdvana acp`, the Agent Client Protocol agent | `cli`, `mcp`, `core` and below |
| `ui` | the Textual TUI and its slash command handlers (`ui/slash`) | `cli`, `core` and below |
| `cli` | startup, the composition root (`cli/bootstrap.py`), the sub-commands (`cli/commands`) and the modules only they use | everything but `main` (`ui` only to start the TUI) |
| `main` | the Typer entry point | everything |

## Core subpackages

`core/tool.py` (the tool base class and registry) is the only module at the top of `core`.

| Subpackage | Owns |
|-|-|
| `config` | the settings model and its sources, runtime paths, data migration, managed policy, model choice for a spec |
| `hooks` | the lifecycle event engine, shell command hooks, user hook modules |
| `state` | sessions, checkpoints, todos, goals, rewind marks, the run store, shared cost budgets, concurrency slots, the run signal counters |
| `context` | the system prompt, NIRNA.md, skills, memories, user slash commands, the tool index, token estimation, compaction and observation masking |
| `safety` | the permission policy and approvals, the action classifier, the sandbox and egress proxy, secret masking, profiles, edit guards |
| `telemetry` | the usage and cost ledger (`analytics`), OpenTelemetry traces, the prompt cache watch |
| `execution` | running a batch of tool calls and the checks around it |
| `loop` | the agent loop and what drives it: run limits, model failover and provider recovery, recovery and activity hooks, the goal and plan gates, the advisor, cancellation, sub-agent loop settings |
| `delegation` | sub-agents, swarms, declared workflows, task tracking, worktrees |
| `contract` | the task contract: what a task claims, the digest that pins it, the test manifest it is judged by and the verification receipts |
| `campaign` | multi-repository campaigns: the manifest of repository tasks, the checkpoint written as work moves on, the isolated worktrees each task runs in and the rollback of a task whose verification failed |
| `evidence` | the self-contained evidence bundle an auditor reads without the repository: the contract and receipt digests, the diff, the static analysis and test output, the verdict and the checksum that pins them (`core/evidence/receipt_bundle.py`) |
| `recommend` | routing one migration task: the deterministic AST pass, or a surgical LLM pass on the high-reasoning tier for critical or wide-context work and the fast standard tier otherwise, each with its estimated cost (`core/recommend/migration_router.py`) |

`core.evidence` and `core.recommend` import nothing else from `core`, so they are placed last in the direction
below, where they break no rule of it.

Allowed direction, lowest first: `contract`, `config`, `hooks`, `state`, `context`, `tool`, `safety`, `telemetry`,
`execution`, `loop`, `delegation`, `campaign`, `evidence`, `recommend`. A subpackage imports only those before it.
Four rules are tested
(`tests/contracts/test_core_subpackages.py`): `config` imports nothing else from `core`; `context`,
`safety` and `telemetry` never import `loop`; only `loop` and the `tools` package import `execution`;
the subpackages form no import cycle.
