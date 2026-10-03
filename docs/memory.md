# Project memory

NerdVana keeps notes that outlive a session in three places.

| Scope | Where | Notes |
|-|-|-|
| `project_rule` | a section appended to `<project>/NIRNA.md` | Loaded into the system prompt with the rest of `NIRNA.md`, so a rule takes effect on the next run. |
| `project_knowledge` | `<project>/.nerdvana/memories/<name>.md` | Plain text. Names may be slash namespaces (`auth/login/rules`). |
| `user_global` | `~/.nerdvana/memories/global/<name>.md` | Plain text, shared by every project. |

`agent_experience` is not stored here, and `WriteMemory` refuses that scope.

The prompt carries only a count of the file-backed memories. The agent reads a memory body only when it calls `ReadMemory`.

## Timestamps and source

Every file-backed entry records when it was created, when it was last modified, where it came from and when it was last read. The record lives in `.index.json` next to the entries of each scope; the `.md` files stay exactly what was written.

| Field | Meaning |
|-|-|
| created | First write of the name. A rename and an overwrite keep it. |
| modified | Last write or edit. A file changed behind the index (an editor, a merge) reports its own modification time. A fresh checkout, which resets every file time, keeps the recorded time while the content is unchanged. |
| source | `user` (`nerdvana memory add`, `/memory`), `agent` (a memory tool call or an approved proposal) or `import` (`nerdvana memory add --source import`). An entry written before the index existed shows `unknown`. An edit keeps the source of the entry. |
| last read | Set by `ReadMemory`. Listing, diffing, renaming and editing do not count. |

`nerdvana memory list`, `/memories` and the `ListMemories` tool show the modified time and the source. Commit `.index.json` with the memories, or ignore it, as you prefer; without it every entry reads as unknown and dated by its file.

## Reviewing what the agent proposes

By default the memory tools write at once. With

```yaml
memory:
  review: true
```

a `WriteMemory`, `EditMemory`, `DeleteMemory` or `RenameMemory` call stores nothing. It becomes a proposal file in `<project>/.nerdvana/memory-inbox/`, and the agent is told that it awaits the user's review. Until you approve it, the proposal is not injected into any prompt, not counted in the session hint, and invisible to `ReadMemory` and `ListMemories`. An edit is queued as a write of the edited text, and a rename as a write of the new name followed by a deletion of the old one.

```
nerdvana memory inbox            # list the proposals with a diff
nerdvana memory approve <id>     # apply one
nerdvana memory approve --all    # apply every pending proposal, oldest first
nerdvana memory reject <id>      # drop one
```

The same commands exist inside the TUI as `/memory inbox`, `/memory approve <id>`, `/memory approve --all` and `/memory reject <id>`.

The listing marks each proposal as `new` (no such entry), `changed` (a unified diff against the entry as it is now), `removed` (the entry would be deleted) or `unchanged`. An approved write is recorded with the source `agent`. A proposal that cannot be applied (for instance a deletion of an entry that is already gone) stays in the inbox and is reported; `approve --all` goes on with the others.

Limits: the inbox holds 200 proposals; a further one is refused until some are decided. A proposal's name and content obey the same rules as a direct write (see below), and are checked again when it is approved. The inbox belongs to the project directory, so run the review commands from the project; a proposal for the `user_global` scope also waits there.

The review covers the memory tools. It does not stop an agent that has `FileWrite` or `Bash` and the permission to use them from writing under `.nerdvana/`; keep `.nerdvana/` out of reach with `sandbox.edit_scope` and the permission rules if that matters, and do not allow-list `nerdvana memory approve` for the agent's shell. The native agent session does not register the memory tools; they are offered by `nerdvana serve` (MCP), which reads `memory.review` once per client from the config it finds from its working directory.

## Forgetting

```
nerdvana memory forget <name>            # asks first; --yes skips the question
nerdvana memory stale --days 90          # list, change nothing
nerdvana memory stale --days 90 --remove # list, ask, then delete the listed entries
```

`stale` lists entries that were not modified for the given number of days (default 30) and that no `ReadMemory` call has read since the index began. Entries written before the index existed count as never read until the first read. In the TUI, `/memory forget <name>` and `/memory stale --remove` only describe what they would remove until `--yes` is added. `nerdvana memory remove` and the `DeleteMemory` tool are unchanged and are not logged.

## Audit log

Every approval, rejection and forget appends one JSON line to `~/.nerdvana/logs/memory-audit.jsonl` (under `$NERDVANA_DATA_HOME` when set): `ts`, `action` (`approve`, `reject` or `forget`), `name`, `scope`, `proposal` and `change` (`new`, `changed`, `removed` or `unchanged`) for the first two, and `project`. A failed approval writes nothing.

## Names and sizes

A memory name is a relative path of letters, digits, dot, underscore, hyphen and slash, at most 200 characters. These are refused: absolute paths, drive letters, `..`, percent-encoded characters (`%2e%2e`), backslashes, empty segments (`a//b`), dot-prefixed segments (`.hidden`) and any name whose resolved path leaves the scope directory, a symlink included. Content is limited to 64 KiB. The name of a `project_rule` section must be a single line of at most 200 characters.
