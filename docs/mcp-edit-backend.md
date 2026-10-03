# NerdVana as an edit backend for other agents

`nerdvana serve` exposes the edit-integrity tools of NerdVana over MCP, so another coding agent can use them in
place of its own file tools.

| Tool | Needs | What it does |
|-|-|-|
| `FileRead` | role `read-only` | Reads a file and tags every line with an anchor `N#hhhhhh` (line number and a hash of the content) |
| `FileEdit` | role `edit`, `--allow-write`, `confirm: true` | Replaces the line an anchor names, or an exact `old_string`; refused when the file was not read by this client or changed since it was read |

The checks are the same as in the agent loop: the anchor must still name the line (it is found again within 20 lines when
earlier edits moved it), writes go through a temporary file and an atomic rename, symbolic links and paths outside the project
are refused. Each client (the identity the ACL sees) has a read ledger of its own, so one client's read never vouches for
another client's edit: an edit by a client that did not read the file, or read it before somebody else changed it, comes back
with an error saying so. When a language server is available, an edit also reports the errors that were not there before it.

`FileRead` and `FileEdit` are in the default `read-only` and `edit` roles of the access-control list; see
[mcp-quota.md](mcp-quota.md) for quotas, and the server's audit log records every call.

```bash
nerdvana serve --allow-write                 # stdio
nerdvana serve --transport http --allow-write
```

The ledger lives in the server process: it starts empty when the server restarts, and a client has to read again.
