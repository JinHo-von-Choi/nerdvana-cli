# Managed policy

An administrator can set limits for a whole machine that no user, project or command line
option can loosen. They are YAML files dropped into a directory, applied above every
setting described in [configuration.md](configuration.md).

## Where the files are

NerdVana reads every `*.yml` and `*.yaml` file (hidden files and other extensions are
ignored) from:

1. `/etc/nerdvana/managed-settings.d/`, the system directory. It may be absent.
2. The directory named by `NERDVANA_MANAGED_DIR`, when that variable is set. It must exist.

Files are read in lexical order of the file name (`10-models.yml` before `20-mcp.yml`);
when both directories hold the same name, the system file comes first. Make the system
directory and its files owned by root and not writable by users.

## What a file can say

```yaml
model:
  allowed_models: ["claude-*", "anthropic:*"]
  denied_models:  ["*-preview"]
mcp:
  allowed_servers: ["docs", "team-*"]
hooks:
  allow_project_hooks: false
permissions:
  always_deny: ["Bash(curl *)", "Bash(sudo *)"]
session:
  max_cost_usd: 5
sandbox:
  mode: auto
```

| Key | Effect |
|-|-|
| `model.allowed_models` | Glob list. A model that matches none of the patterns is refused. An empty list is an error; leave the key out to allow every model. |
| `model.denied_models` | Glob list. A model that matches any pattern is refused, even if it is also allowed. |
| `mcp.allowed_servers` | Glob list of MCP server names. A configured server outside it is not started. An empty list blocks every server. |
| `hooks.allow_project_hooks` | `false` forces project hooks and project skills off whatever the user set. `true` changes nothing; the user keeps the choice. |
| `permissions.always_deny` | Rules added to the user's `always_deny`. They cannot be removed, and deny rules win over `always_allow`. |
| `session.max_cost_usd` | Ceiling on the estimated cost of a session in USD, above 0. A user value above it, or no value, becomes this number; a lower user value stays. Like the setting itself, it needs a known price for the model. |
| `sandbox.mode` | Floor for shell command confinement (`off`, `auto`, `require`, weakest to strictest). A weaker mode is raised to it. Quote `"off"` in YAML, which otherwise reads it as false. |

A model pattern is matched, as a glob and case sensitively, against the model name and
against `provider:model`, so `anthropic:*` allows every Anthropic model.

## How files combine

Every key only restricts, so the combination is the strictest one and the order of the
files never decides a result:

- allow-lists (`model.allowed_models`, `mcp.allowed_servers`): a value must satisfy the
  list of every file that has one
- `model.denied_models` and `permissions.always_deny`: added up
- `session.max_cost_usd`: the smallest value
- `sandbox.mode`: the strictest mode
- `hooks.allow_project_hooks`: `false` wins

## Where it is enforced

- At startup: the limits are applied when the configuration loads and again after the
  command line options (`--model`, `--sandbox`, `--max-cost-usd`, `--set`), so an option
  cannot undo them. A model outside the policy ends the start with exit code 2 and a
  message that names the file and the key that refused it.
- `/model` and `/provider` in the REPL refuse a model outside the policy and say why.
  The model a sub-agent asks for is refused the same way and the parent's model is used.
- `model.fallback_models`, `session.escalation_model` and `agents.categories` entries
  outside the policy are dropped.
- MCP servers outside `mcp.allowed_servers` are left out when the server list is read.

## A broken file stops the start

A file that cannot be read, is not valid YAML, has an unknown key or a value of the wrong
type is never skipped, because skipping it would drop its limits. The CLI refuses to start
and prints the exact error of every broken file, for example:

```
Invalid configuration: managed policy: /etc/nerdvana/managed-settings.d/10-a.yml: sandbox.mode: expected one of off, auto, require
```

## Seeing what applied

- `nerdvana doctor` has a `managed_policy` check that lists the files that applied, or the
  error of each one that failed to parse.
- `/policy` in the REPL lists the files and what every control did, marking the ones that
  changed a user setting.
- Each start that has managed files appends one JSON line to `~/.nerdvana/logs/managed-policy.jsonl`
  (under `NERDVANA_DATA_HOME` when set) with the time, the files, every applied key with its
  value and source files, whether it changed a setting, and the reason when a model was
  refused.

## Limits

The policy is enforced by this program. It stops a user from loosening settings through
NerdVana's own configuration, command line and commands; it does not stop someone with root
on the machine, who can edit the files or the installed code, or someone who uses another
client. `session.max_cost_usd` and `sandbox.mode` are only as strong as the features they
configure: a cost ceiling does not apply to a model without a known price, and confinement
needs Landlock (see [sandbox.md](sandbox.md)).
