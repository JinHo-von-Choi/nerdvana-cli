# Workflows

A workflow is a YAML file that declares a set of agent steps and how they depend on each other. The same
file always runs the same steps in the same order: the model does not write the orchestration, it only
fills in what the steps ask. Workflows are for work that is too wide for one agent but has a known shape:
review every changed file, check every claim, run a fixed pipeline of read-only passes.

```
nerdvana workflow list
nerdvana workflow show review-fanout
nerdvana workflow run review-fanout --input base=main --max-cost-usd 2
nerdvana workflow run review-fanout --resume 20261003-141500-a1b2c3
```

## Where workflows are found

In order of precedence, a later one replacing an earlier one of the same name:

1. the ones bundled with nerdvana (`review-fanout`),
2. `~/.nerdvana/workflows/*.yml`,
3. `<project>/.nerdvana/workflows/*.yml`.

`nerdvana workflow list` names the origin of each and reports files that could not be loaded. A definition
is validated completely when it is loaded: a typo in a reference, a cycle, an unknown key or a missing
schema is reported before any agent is paid for.

A project workflow is code that comes with the repository: its `verify` steps run shell commands. Read a
workflow from a project you do not trust before you run it, as you would a script.

## The format

```yaml
name: review-fanout
description: Review the changed files in parallel.
allow_write: false              # default; see "Writing"
result: summary                 # step whose output is the answer; default: the last step
inputs:
  base:
    default: HEAD               # no default means the input is required
    description: Revision the working tree is compared with
steps:
  - id: files
    kind: verify
    command: git diff --name-only ${inputs.base}
  - id: review
    agent: code-reviewer
    foreach: ${steps.files.items}
    prompt: Review ${item} for defects. Answer with JSON {"items": [...]}.
    output: json
    schema: {type: object, required: [items]}
```

`inputs` maps a name to a default value, or to `{default, description}`. Values given with `--input k=v`
(or by the model) replace the defaults; an unknown input is an error.

### Steps

Every step has an `id` (letters, digits, `-` and `_`, starting with a letter) and a `kind`. Which steps may
run is a graph: a step waits for the steps in its `needs` list and for every step its templates refer to
with `${steps.<id>...}`, so most workflows never write `needs`. Steps with nothing between them run at the
same time.

Kind `agent` (the default) runs one sub-agent:

| Key | Meaning |
|-|-|
| `agent` | Agent type: `general-purpose` (default), `Explore`, `Plan`, `code-reviewer`, `test-writer`, `git-management` or a custom type from `.nerdvana/agents` |
| `prompt` | The task, a template (see below) |
| `foreach` | One reference to a list, `${steps.<id>.items}`: one agent per element, run in parallel |
| `max_turns`, `category`, `model` | As for the `Agent` tool; the agent type's values apply when absent |
| `write_scope` | `none` (default), `project` or a list of paths; needs `allow_write: true` |
| `output` | `text` (default) or `json`; `json` needs a `schema` |
| `schema` | JSON Schema the answer must satisfy: `type`, `enum`, `required`, `properties`, `items`, `minItems` |

Kind `verify` is a check: it runs a shell command through the same code as `/goal` (`core/loop/verify.py`), under the sandbox policy of the session, and passes when the command exits with status 0. A failing check stops the run and the error carries the end of its output. Keys: `command` (a template) and `timeout` in seconds (default `goal.verify_timeout`). Its output is kept up to 20000 characters from the end. A check always runs again on resume, so what later steps read is current.

Kind `cross_check` has independent reviewers judge a list of claims: `claims` is one reference to a list (`${steps.review.items}`), `reviewers` is how many agents judge it (1 to 9, default 3), `agent` defaults to `general-purpose`, and `prompt` adds instructions. Each reviewer sees all claims numbered, is told to try to refute each one by reading the code, and answers with one verdict per claim; an answer that skips or repeats a claim is sent back. A claim is kept only when a strict majority of the reviewers confirmed it. The step's output is JSON with `confirmed` (the surviving claims) and `unverified` (each other claim with its `votes`, the number of `reviewers` and their `reasons`), so a later step can report what could not be confirmed as unverified rather than lose it. Reviewers are always read-only.

### Templates

A template is the text of a `prompt`, `command`, `foreach` or `claims`. A `${...}` in it is replaced:

| Reference | Value |
|-|-|
| `${inputs.name}` | An input |
| `${steps.<id>.output}` | The text a finished step produced; for `output: json` the answer as formatted JSON; for a `foreach` step the outputs joined, or a JSON array for `json` |
| `${steps.<id>.items}` | The list a step produced, as JSON in a prompt and as a list in `foreach` or `claims` |
| `${item}`, `${item.key}`, `${item_index}` | The element a `foreach` agent handles; `.key` reads into a mapping, `.0` into a list |

The `items` of a step are: for `output: json`, the bare array, or the `items` array of the answer object (or the object itself when it has none), joined over all elements of a `foreach`; for text and `verify` steps, the non-empty lines of the output; for `cross_check`, the confirmed claims. So `git diff --name-only` feeds a `foreach` directly.

A reference that names nothing is an error. In a `command`, every value is quoted for the shell, so an input can never add a command of its own.

### Writing

Workflows are read-only. Every agent runs with `write_scope: none` unless its step names another scope and the file sets `allow_write: true`; a definition that names a scope without `allow_write` is rejected at load. `none` removes the writable project and temporary directories for shell commands (the same confinement as an agent type with `write_scope: none`, see [sandbox.md](sandbox.md)) and refuses the edit tools. An agent type that is already read-only, such as `code-reviewer`, stays read-only whatever the step asks. `write_scope` governs agents only: a `verify` command is written in the file and runs with the sandbox policy of the session, so it can write wherever that policy allows. Parallel writes are not merged: give writing steps different paths, or run them one after another.

## Execution

Steps run as soon as everything they need has finished. All agents of a run share `workflow.max_parallel` slots (default 4), never more than `session.max_parallel_agents`. A `foreach` longer than `workflow.max_agents` (default 50) stops the run before any of its agents starts.

An agent step with `output: json` is validated against its schema. When the answer has no JSON or breaks the schema, the agent is asked again with the problems and its previous answer, up to 3 times; after that the step fails.

A step that fails (an agent error, a failing check, an answer that never validates) stops the run after the agents already running have finished; steps that do not depend on it and had already started are kept.

### Cost

The run has one ceiling: `--max-cost-usd`, or `session.max_cost_usd` when that is not given, or the share a session reserves for it when the model calls the tool (`session.subagent_budget_fraction` of what is left). It is tracked with the same envelopes as sub-agents (`core/state/budget.py`): an agent starts with `1 / max_parallel` of what is left of the ceiling, stops when it has used that, and its actual spend is charged when it finishes, the unused part going back. When nothing is left, or an agent used up its share, no further agent starts and the run ends as `stopped` (exit code 3). Without a ceiling there is no limit.

### Results and resume

Every run has an id (`20261003-141500-a1b2c3`). Under `~/.nerdvana/workflows/runs/<run-id>/` it keeps `run.json` (workflow, inputs, status, cost) and one `step-<id>.json` per step with the units that finished: one agent run each (an element of a `foreach`, a reviewer of a `cross_check`). Files are replaced atomically, so a killed run leaves whole files.

A unit is stored under a key: the hash of its agent, rendered prompt, limits and schema together with the results of the steps it needs. `--resume <run-id>` runs the workflow again with the stored inputs (a given `--input` overrides one) and reuses every stored unit whose key is unchanged, so a step interrupted at element 30 of 40 runs only the other ten. A changed input or a changed upstream result changes the keys downstream and runs those units again. A failed unit is never reused.

## The Workflow tool

With `workflow.enabled: true` the model gets a `Workflow` tool taking a workflow `name` and its `inputs`. It runs the named workflow and returns the final step's output with the run id; a run that stops returns an error that names the run id and the `nerdvana workflow run <name> --resume <run-id>` command. Every call asks for confirmation. Sub-agents never get the tool. The token and signal totals of every agent are added to the calling session's totals, and the run counts against its cost limit as described above. The tool exists only when the setting is on; `nerdvana workflow run` does not depend on it.

## Settings

See the `workflow` section of [configuration.md](configuration.md): `enabled`, `max_parallel`, `max_agents`.

## Exit codes of `nerdvana workflow run`

| Code | Meaning |
|-|-|
| 0 | Completed; the final output is printed |
| 1 | A step failed; the run id and the resume command are printed |
| 2 | Invalid workflow, inputs, run id or configuration |
| 3 | Stopped by the cost ceiling |

## The bundled `review-fanout`

Input `base` (default `HEAD`). It lists the files that differ from `base` with `git diff --name-only`, has one `code-reviewer` agent read each file and report concrete problems as JSON, has three independent `code-reviewer` agents try to refute those claims, keeps the ones a majority confirmed, and has an `Explore` agent write the summary with the unconfirmed claims listed as unverified. Everything is read-only.
