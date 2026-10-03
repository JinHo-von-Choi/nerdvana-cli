# Agent Client Protocol (ACP) agent mode

`nerdvana acp` runs NerdVana as an agent of the [Agent Client Protocol](https://agentclientprotocol.com): an editor starts the command, talks JSON-RPC to it over standard input and output, and shows the conversation, the tool calls and the permission questions in its own interface. The agent loop, the tools, the permission policy, the sessions and the cost accounting are the ones `nerdvana run` and the terminal UI use; only the front end differs.

The protocol version is 1. Version 2 of the protocol is still a draft and is not implemented: a client that asks for version 2 is answered with version 1.

## Install

The command needs the optional extra that pulls in the Python SDK (`agent-client-protocol`):

```bash
pip install "nerdvana-cli[acp]"
# or, for the tool install
uv tool install "nerdvana-cli[acp]"
```

Without the extra, `nerdvana acp` prints the install hint on standard error and exits with status 2.

## Zed

Add the agent to Zed's `settings.json` (Agent Settings, External Agents, Add Custom Agent opens the file):

```json
{
  "agent_servers": {
    "NerdVana": {
      "type": "custom",
      "command": "nerdvana",
      "args": ["acp"],
      "env": {
        "ANTHROPIC_API_KEY": "sk-ant-..."
      }
    }
  }
}
```

The key variable is the one of the configured provider (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, and so on); leave `env` empty when the key is already in the environment Zed starts the command from or in the NerdVana configuration. Options go in `args`:

```json
"args": ["acp", "--model", "claude-sonnet-5-5", "--approval-mode", "default", "--set", "session.max_cost_usd=2"]
```

Other ACP clients start the same command.

## Options

| Option | Meaning |
|-|-|
| `--config`, `-c` | Configuration file, as for `nerdvana run` |
| `--model`, `-m`, `--provider`, `-p` | Model and provider for every session |
| `--approval-mode` | `default`, `auto_edit`, `yolo` or `plan`; `default` asks the editor before anything the policy marks as needing approval |
| `--set section.field=value` | Override one setting for every session (repeatable), as for `nerdvana run` |

A configuration that is invalid is reported before the agent starts serving. A missing API key is reported when a session is created, as the JSON-RPC error "Authentication required" with the reason in its data.

## What is implemented

| Protocol method | Behaviour |
|-|-|
| `initialize` | Version 1. Capabilities: `loadSession`, prompt images and embedded context (no audio), MCP over HTTP and SSE. No authentication methods |
| `session/new` | Creates a session in the given `cwd` (must be an absolute, existing directory) and connects the MCP servers described below. The session id is the NerdVana session id |
| `session/load` | Loads a session recorded in `~/.nerdvana/sessions` (by this mode, the terminal UI or `nerdvana run`) and replays its messages as `user_message_chunk` and `agent_message_chunk` before answering, then continues the conversation. An unknown or unsafe id is "Resource not found" |
| `session/prompt` | Runs one turn; answers with the stop reason and, in `usage`, the tokens the turn used (input, output, cache read, cache write) |
| `session/cancel` | Cancels the running turn; the pending `session/prompt` answers `cancelled` |
| `session/request_permission` (agent to client) | See Permissions |

Updates sent while a turn runs:

| Update | Source |
|-|-|
| `agent_message_chunk` | The model's text, streamed. A warning of the agent loop (a limit that stopped the run, say) is appended as text |
| `agent_thought_chunk` | The model's reasoning, only when `model.show_thinking` is true (the default) and the model produces any |
| `tool_call`, `tool_call_update` | One per tool call, see Tool calls |
| `plan` | Each `TodoWrite` call, as the full list of entries with their status (priority is always `medium`) |
| `usage_update` | After every model request: tokens in the context (`used`), the context window (`size`) and the cumulative session cost in USD |
| `available_commands_update` | Sent once after `session/new` and `session/load`: the user commands, see Slash commands |

Prompt content: text, resource links (`[name](uri)`), embedded text resources (wrapped as `<context uri="...">`) and images (at most six, PNG, JPEG, GIF or WebP). Embedded binary resources and audio are not attached; the prompt says so in a short bracketed note.

### Tool calls

Each call the model makes is announced as `tool_call` with status `pending` before it runs, moves to `in_progress` once it passed the permission check, and ends as `completed` or `failed` with the result as text content (cut at 10000 characters; the model gets the whole result).

| Tool | Kind |
|-|-|
| `FileRead`, `symbol_overview`, `lsp_diagnostics` | `read` |
| `FileWrite`, `FileEdit`, `replace_symbol_body`, `insert_before_symbol`, `insert_after_symbol`, `lsp_rename` | `edit` |
| `safe_delete_symbol` | `delete` |
| `Glob`, `Grep`, `find_symbol`, `find_referencing_symbols`, `lsp_find_references`, `lsp_goto_definition`, `ToolSearch`, `WebSearch` | `search` |
| `Bash`, `Parism` | `execute` |
| `WebFetch` | `fetch` |
| everything else, MCP tools included | `other` |

Calls of kind `read`, `edit` and `delete` carry the file as an absolute location. `FileWrite` carries a diff of the whole content against the file as it is now (nothing when the file is missing, larger than 1 MB or not text), and `FileEdit` with `old_string` and `new_string` carries that pair as a diff. The title is a verb and the command, path or URL, for example `Run git status` or `Edit src/app.py`.

### Permissions

When the permission policy says a call needs approval, the loop's confirm callback sends `session/request_permission` with the tool call (title, kind, location, input and the reason, including the preview of an edit) and three options:

| Option | Effect |
|-|-|
| `allow_once` | The call runs |
| `allow_always` | The call runs, and the same tool with the same main argument (the same command, path or URL) is allowed for the rest of the session without asking again. The memory is not written to the configuration; `nerdvana approvals` is the way to make a rule permanent |
| `reject_once` | The call does not run and the model is told the user refused |

A `cancelled` outcome, a failed request and a closed connection all count as a refusal. Rules in `permissions` (`always_allow`, `always_deny`) and the blocklists of the tools apply before the editor is asked, so a denied call never reaches the editor.

### Stop reasons and errors

| Loop outcome | Answer of `session/prompt` |
|-|-|
| finished | `end_turn` |
| verification of a goal not met after its attempts | `end_turn` |
| turn limit (`session.max_turns`) | `max_turn_requests` |
| response cut off by the token limit | `max_tokens` |
| cost limit, total token limit, cost limit without a known price | `refusal`, with the loop's message as text |
| `session/cancel` | `cancelled` |
| provider failure that retries and fallbacks did not resolve | JSON-RPC error `-32603` with the provider's message in `data.details` |

A second `session/prompt` while one is running in the same session is refused (`-32600`); an unknown session id and a bad `cwd` are `-32602`. After a failed turn the session is still usable.

### Slash commands

The user commands (`<project>/.nerdvana/commands/*.md` and `~/.nerdvana/commands/*.md`, see `core/context/user_commands.py`) are published as available commands, with their description and a free-form argument hint. A prompt that starts with `/<name> arguments` is expanded into the template before it reaches the model. Built-in commands of the terminal UI (`/model`, `/clear` and so on) and skills are not exposed; a prompt that starts with an unknown `/word` goes to the model as typed.

### MCP servers

The `mcpServers` of `session/new` and `session/load` (stdio, HTTP and SSE) are connected for that session and disconnected when the connection ends. They are added to the servers of `~/.nerdvana/mcp.json` and `<cwd>/.mcp.json`; on a name clash the editor's entry wins. A server that fails to connect is logged on standard error and the session starts without it.

### Standard output

Standard output carries protocol frames only. The agent keeps private copies of descriptors 0 and 1 for the protocol and points the process's own standard output at standard error and its standard input at the null device, so a stray print from a library, a tool or a child process cannot corrupt a frame, and a child process cannot read the editor's messages. Diagnostics and logs are on standard error.

## Limits

- The editor's file system (`fs/read_text_file`, `fs/write_text_file`) and terminals (`terminal/*`) are not used, even when the client offers them. Reads, writes and shell commands go through the local tools, so unsaved editor buffers are not seen and a command's output is returned to the model, not streamed into an editor terminal. Mapping the tools onto those methods would change the hashline, read-ledger and sandbox guarantees of the file and shell tools.
- Not implemented: session modes, session config options, `session/list`, `session/resume`, `session/fork`, `session/close`, authentication methods, elicitation (the `AskUser` tool answers that no user is available), additional workspace directories (a session has one `cwd`), audio and binary resources in prompts.
- A running tool call is `in_progress` until it ends; its output is delivered when it finishes, not while it runs. Calls that run concurrently report their end together.
- `session/cancel` stops the model request and the running tool; the loop's history keeps the user message of a cancelled turn.
- Project configuration (`nerdvana.yml`), commands, skills and the language-server tools use the session's `cwd`. The process's own working directory is not changed, because one process serves sessions of different directories; agent types defined under `.nerdvana/agents` are read from the directory the editor started the agent in, which for Zed is the project root.
- The agent was exercised against the SDK's own client over an in-memory connection and over a real stdio subprocess, and with hand-written frames. It has not been run against Zed or another editor in this repository's tests, and it has not been run on Windows.
