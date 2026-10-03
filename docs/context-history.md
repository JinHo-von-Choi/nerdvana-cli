# Context breakdown and history search

## Where the context window goes

`/context` in the interactive session and `nerdvana context [session-id]` on a stored session print what the
next request is made of and how that compares with `session.max_context_tokens` and the compaction threshold
(`session.compact_threshold` of the window):

- the system prompt by part: base instructions, skills catalog, deferred tool index, environment, git summary,
  project documents (NIRNA.md, AGENTS.md, CLAUDE.md), and in a live session the session context (workspace
  snapshot, memory hint, hook output), the agent role and the active skill;
- the tool declarations, one line per tool, largest first, and how many tools are deferred behind ToolSearch;
- the conversation by role, and the tool results by tool name;
- notes: the parts that are 10 percent or more of the context, a warning once the compaction threshold is
  passed, and whether observation masking would help.

All figures are the loop's own estimate (the same one that decides when to compact), not a provider's count.

Observation masking clears old results of read-type tools (file reads, searches, shell output) once they add up
to `session.mask_trigger_tokens`. The report says how many tokens that would clear now. When
`session.observation_masking` is off and the figure is large it suggests turning it on; when it is on but has
not reached its trigger it says so.

`/context` with no argument prints the breakdown and then the context profile. `/context usage` prints only the
breakdown. `/context list` and `/context <name>` work on the profile as before.

`nerdvana context` rebuilds a loop for a stored session. The conversation is read from the transcript, the
system prompt and the tool declarations come from the current settings and tools (MCP tools are not connected),
and the session context of a live run is not part of it. A transcript keeps only the first 500 characters of
each tool result, so the tool results of a stored session are understated. `--top N` sets how many tools and
tool results are listed, `--json` prints the report as data.

## Searching past sessions

```
nerdvana history search "retry logic" --since 7d --cwd .
```

Prints one line per matching message, newest first: session id, date, role (`user`, `assistant`, `tool`) and
the text around the match. Every word of the query must be in the message, in any order.

- `--since` takes `Nd`, `Nh` or `all`.
- `--cwd DIR` keeps the sessions that were started in `DIR` or below it. A transcript records its directory
  when it is created; transcripts from earlier versions have none and are left out when `--cwd` is given.
- `--limit` caps the lines (default 20).

`/history <query> [--since 7d] [--cwd DIR]` does the same inside the session.

With SQLite FTS5 the search uses a small index, `history-index.sqlite` in the data root (`$NERDVANA_DATA_HOME`,
default `~/.nerdvana`). It is brought up to date on every search by reading only what was appended to the
transcripts since the last one, and it drops sessions whose transcript was deleted. The index matches whole
words and word prefixes. When FTS5 is missing or the index cannot be used, every transcript is scanned for the
words as substrings instead; the result lines have the same shape. The index can be deleted at any time.

User prompts and file contents are written to a transcript as they are, so the search passes everything through
the secret masker (`docs/secret-masking.md`) before it is indexed or shown: key and token shapes and the values of
credential-named environment variables appear as `[REDACTED]`, and the index file holds only the masked text.
