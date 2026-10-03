# MCP servers as a client

NerdVana CLI connects to external [MCP](https://modelcontextprotocol.io/) servers over stdio or streamable HTTP and registers what they offer: their tools as `mcp__{server}__{tool}` and their skills in the skill catalog. This page describes how the connection behaves. The server side (`nerdvana serve`) is described in [mcp-edit-backend.md](mcp-edit-backend.md) and [mcp-quota.md](mcp-quota.md).

The connection is made by the `Client` of the `mcp` Python SDK (2.x), wrapped in `nerdvana_cli/mcp/client.py`. The SDK is the optional `mcp` extra (`pip install 'nerdvana-cli[mcp]'`). Without it a session still starts: servers that are configured are reported as not connected, and everything else works.

## Declaring servers

Servers are declared in JSON, not in `nerdvana.yml`: `~/.nerdvana/mcp.json` (global) and `<cwd>/.mcp.json` (project, overrides a global entry of the same name). `${VAR}` in `env`, `url`, `headers` and `write_paths` expands to the environment variable, or to an empty string when it is unset.

```json
{
  "mcpServers": {
    "files": {
      "type": "stdio",
      "command": "node",
      "args": ["server.js"],
      "env": { "API_KEY": "${FILES_API_KEY}" },
      "sandbox": "auto",
      "write_paths": ["~/.cache/files-server"],
      "network": true
    },
    "search": {
      "type": "http",
      "url": "https://search.example.com/mcp",
      "headers": { "Authorization": "Bearer ${SEARCH_KEY}" }
    }
  }
}
```

| Field | Applies to | Default | Description |
|-|-|-|-|
| `type` | all | `stdio` | `stdio`, `http` or `sse`. `sse` and `http` both use streamable HTTP; the older HTTP+SSE transport is deprecated by the specification. |
| `command`, `args` | stdio | | The server process and its arguments. |
| `env` | stdio | `{}` | Added to the environment nerdvana runs in, which the server inherits. |
| `url` | http | | The endpoint. A plain `http://` URL to a host other than localhost logs a warning. |
| `headers` | http | `{}` | Sent with every request; this is where a bearer key goes. |
| `sandbox` | stdio | `off` | `off`, `auto` or `require`; see "Confining a stdio server". |
| `write_paths` | stdio | `[]` | Paths a confined server may write, besides `/tmp`, `/var/tmp`, the system temporary directory and `/dev`. `~` expands; a relative path is relative to the directory nerdvana was started in. |
| `network` | stdio | `true` | `false` also refuses TCP connections and binds from the confined server; needs Linux 6.7 (Landlock ABI 4). |

`nerdvana mcp add`, `nerdvana mcp list` and `nerdvana mcp remove` edit the global file. `/mcp` in the REPL shows which servers are connected and how each stdio server is confined.

## Protocol revisions

The client asks a server to `server/discover` first, which is how a server of the 2026-07-28 revision answers (stateless: no `initialize` handshake and no session id). A server that does not know that method is reached with the `initialize` handshake of the 2025 revisions. Nothing needs configuring and both kinds of server work; `tests/mcp/test_client_protocol.py` connects to one of each, and `tests/mcp/test_client_nerdvana_server.py` connects to `nerdvana serve` over stdio and over HTTP with a bearer key.

## Requests, limits and failures

- Every request has a timeout of 30 seconds, and so does the connection (the negotiation of the protocol revision). A timeout, a refused handshake, a protocol error and a connection that ended all surface as `RuntimeError` with a message that names the cause. A server that fails to connect does not affect the others.
- One server message is at most 10 MB. A stdio line over that ends the connection (the position in the stream is lost once part of a line is dropped); the next request fails at once with "not connected". An HTTP response body over that is refused while it streams in. An event stream is not capped as a whole, since it lasts as long as the server keeps it open, but a single event is capped at 10 MB.
- A tool result with `isError: true` reaches the model as an error result with the server's text.
- Server processes are stopped with their whole process group: stdin is closed, the server gets two seconds to exit, then it is terminated. A connection that fails midway leaves no process behind.
- A server that does not exit when asked to is killed; its stderr is logged at debug level, not shown.

## Caching of lists

The SDK honours the `ttlMs` and `cacheScope` of a list result (2026-07-28 revision) with an in-memory cache per connection: a second `tools/list` inside the server's `ttlMs` is answered locally. A change notification evicts the entry. A server of the 2025 revision sends no hint and its lists are not cached. Only the first page of a list is cached; the client follows `nextCursor` to the end, up to 100 pages.

## Results that ask for more input

A server on the 2026-07-28 revision can answer a tool call with `input_required`: one or more elicitations (a question with a small form schema) and an opaque state to send back with the answers. The SDK drives the exchange, up to ten rounds. Each question reaches the user through the same channel as the `AskUser` tool, with the property title and description as the question and the enum values (or yes and no for a boolean) as suggested answers; the answer is converted to the property type. A dismissed question is reported to the server as cancelled and an answer that does not fit the type as declined.

When no user can be asked (a one-shot run, a sub-agent, a background task) the call fails with "This MCP server asked for more input, but no user is available to answer in this session". URL-mode elicitation, sampling and roots requests are refused: the specification deprecates sampling and roots, and nerdvana has no way to open a URL for the user.

Limits of what the SDK exposes and what this client does with it:

- A server of the 2025 revision asks its questions as a separate request while the call is open. That request is not tied to the tool call that is waiting, so the client refuses it; only the 2026-07-28 `input_required` form is answered.
- Tasks are not exposed: the 2.x SDK removed its experimental Tasks client, and this client does not advertise the tasks extension, so servers do not return task results to it.
- `subscriptions/listen` (change events) is available in the SDK but not used: tool lists are read when a server connects.

## Skills over MCP

A server that declares the `io.modelcontextprotocol/skills` extension offers skills: directories with a `SKILL.md` and supporting files, listed by `skills/list` and read with `resources/read`. The client side follows the specification of the extension and treats everything a server sends as untrusted.

1. Listing. When a server connects, its skills are listed (metadata only: URI, frontmatter, and a manifest of every file with its SHA-256 digest and size). They enter the skill catalog (see [skills.md](skills.md)) as `server:skill`, so a server's skill never replaces a local skill or another server's, and the slash command is `/server:skill`. A skill name that repeats on one server is numbered (`server:skill#2`). The status line printed when the server connects shows how many skills it offered. No file is read at this point.
2. Activation. When the model calls `ActivateSkill` for the skill, the client reads its `SKILL.md`, checks the byte size and SHA-256 digest against the manifest, and checks that the frontmatter equals the listed frontmatter field by field and that the name matches the skill's directory. Only then do the instructions go into the conversation, wrapped in `<skill_content>` like any skill.
3. Supporting files. The activation result lists the files of the manifest. The model reads one with the `mcp__skill_files__read` tool (skill name and a path relative to the skill, or the full URI). Only a file in the retained manifest of an activated skill can be read; its size and digest are verified before the text is returned, wrapped in `<mcp_skill_file server=... skill=... path=...>`. A file that mentions another skill does not activate it.
4. Verification failures. Content that fails a check is not used and the entry is refreshed with `skills/get`; the activation reports the reason and can be tried again.
5. Approval. A skill whose frontmatter declares `allowed-tools` is loaded only after the user approves it. The question names the server, the skill and the tools, and is put through the `AskUser` channel; without a user the skill is refused. The field stays informational as for local skills: every tool call still goes through the permission system. A skill without `allowed-tools` asks nothing. Approvals are not stored; the question is asked at each activation.
6. Limits. A skill with more than 512 files or more than 16 MiB, one without digests for its files (a `dynamic` manifest), a malformed entry, and a `SKILL.md` over 64 KiB are not offered or not loaded. A server's skill description is cut at 1024 characters in the catalog.

Not implemented: loading a skill by URI that the server did not list (the specification requires hosts to support it; nerdvana has no way for a user to name a URI yet), `resources/directory/read`, and refreshing the listing while a session runs.

## Confining a stdio server

A stdio MCP server is code from a third party that runs as you. With `sandbox` set, nerdvana starts the server through the [Landlock](sandbox.md) launcher that confines the `Bash` tool, so the server and everything it starts can write only to `/tmp`, `/var/tmp`, the system temporary directory, `/dev` and the `write_paths` of that server. The project directory is not writable unless it is listed. With `network: false` and Linux 6.7 or later, TCP connections and binds fail as well.

| `sandbox` | Behaviour |
|-|-|
| `off` | The server starts as before. This is the default, so an existing configuration does not change. |
| `auto` | Confined where the system supports it; otherwise it starts unconfined and a warning is logged. |
| `require` | The connection fails when the server cannot be confined. |

A value other than these, or a `network` that is not `true` or `false`, makes the connection fail with a message naming the server instead of falling back to a default.

What it does not do is what sandbox.md lists for the `Bash` tool: it does not stop reading (a confined server can read any file you can and send it over a connection that is allowed), it does not restrict running programs, UDP or local sockets, and it is Linux only. The server also still inherits the environment of nerdvana, as before; keep secrets that a server should not see out of that environment. HTTP servers run elsewhere, so the setting does not apply to them.

Where it is reported:

- `/mcp` shows `confined (...)`, `unconfined (...)` with the reason, for each connected stdio server.
- `nerdvana doctor` has an `mcp_sandbox` check, built from the configuration without starting any server: it lists the confined and unconfined servers, warns when a server asks for confinement that this system cannot give, and fails on an unusable setting or on `require` that cannot be met.
