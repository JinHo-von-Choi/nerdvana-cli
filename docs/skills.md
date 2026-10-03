# Skills

NerdVana CLI reads skills in the [Agent Skills](https://agentskills.io/specification) format that Claude Code, Codex, Gemini CLI and other agents share. A skill is a directory holding a `SKILL.md` (YAML frontmatter plus markdown instructions) and, optionally, `scripts/`, `references/` and `assets/`. A single `<name>.md` file with the same frontmatter also works in the nerdvana and `.claude` directories.

## How the model uses skills

Skills load in three steps, so an installed skill costs only a catalog line until it is needed.

1. Catalog. The system prompt has a `Skills` section with one `- name: description` line per skill the model may activate, ordered by name. The section is absent when there is no such skill.
2. Activation. The `ActivateSkill` tool takes a `name` restricted to the catalog and returns the `SKILL.md` body wrapped in `<skill_content name="...">`, followed by the skill directory and a `<skill_resources>` list of the bundled files (relative paths, capped at 200). The files are listed, never read; the model opens them with the file tools when the instructions call for it. Activating the same skill again in a conversation answers that it is already active. `/clear` forgets what was activated.
3. Resources. `scripts/`, `references/` and `assets/` are read or run on demand through the normal tools and the normal permission system.

Users can still start a skill with its slash command (the `trigger` field, default `/<name>`).

## Where skills are found

Lowest precedence first. A later location replaces a skill of the same name and a warning is logged naming both files.

| Location | Notes |
|-|-|
| built-in | shipped with the CLI |
| `~/.claude/skills` | only with `skills.include_claude_skills` |
| `~/.agents/skills` | cross-client convention |
| `~/.nerdvana/skills` | |
| `<project>/.claude/skills` | only with `skills.include_claude_skills`; project trust required |
| `<project>/.agents/skills` | project trust required |
| `<project>/.nerdvana/skills` | project trust required |

Project locations win over user locations, and inside one level the nerdvana directory wins over `.agents`, which wins over `.claude`. A skill directory may sit up to four levels below the location (for example `.agents/skills/team/review/SKILL.md`); at most 2000 directories per location are scanned, and `.git` and `node_modules` are skipped. A directory that holds a `SKILL.md` is one skill and is not searched further.

Symlinks inside a user location may point anywhere (installers link skills into `~/.agents/skills`). A project location must keep its links inside itself: a skill that resolves outside is skipped.

## Project trust

A project is a repository the user may have just cloned, and a skill is text that steers the model, so project skills load only after the project is trusted. The mechanism is the one project hooks use, with the same approval file:

1. Set `hooks.allow_project_hooks: true`.
2. Approve each skill file with `nerdvana skill trust <path>` (a `SKILL.md`, or its directory). `nerdvana hook trust <path>` records the same approval.

An approval is bound to the SHA-256 digest of the file, so editing a `SKILL.md` revokes it until it is approved again. Only the `SKILL.md` text is covered; bundled scripts are not run by the loader and still pass through the permission system when the model runs them. Skills that are not trusted are skipped with a warning in the log.

## Frontmatter

| Field | Handling |
|-|-|
| `name` | Required by the standard: 1 to 64 characters, lowercase letters, digits and hyphens, no leading, trailing or doubled hyphen, equal to the directory name. A violation logs a warning and the skill still loads. A missing name falls back to the directory (or file) name. |
| `description` | Required, up to 1024 characters; say what the skill does and when to use it. A missing or empty description skips the skill. |
| `license`, `metadata` | Read and kept. |
| `compatibility` | Up to 500 characters; shown in the activation result. |
| `allowed-tools` | Experimental field of the standard (space-separated, or a YAML list). Recorded and reported in the activation result. It is not enforced: every tool call still goes through the normal permission system, and a skill cannot grant itself permissions. |
| `trigger` | Slash command for the skill; default `/<name>`. |
| `disable-model-invocation` | Extension of this CLI, not part of the standard. `true` keeps the skill out of the catalog and `ActivateSkill`; its slash command still works. |

Frontmatter that is not valid YAML only because a value contains an unquoted colon (`description: Use this when: ...`) is retried once with those values quoted. Any other YAML error skips the skill. A skill file larger than 64 KiB is skipped.

## Checking what loaded

`/skills` lists the loaded skills in a session. Warnings about names, shadowing and untrusted project skills go to the log.

## Permissions for bundled files and scripts

Reading a skill's bundled files needs no extra permission: the file tools read any path they can open without asking, so `references/` and `assets/` are available after `ActivateSkill` lists them. A script under `scripts/` runs through `Bash` like any other command, with the same permission rules and sandbox. There is no automatic allowance for a skill's directory, because that would let a skill run its own scripts unasked; add a `Bash(...)` rule to `permissions.always_allow` for the scripts you trust.

## Skills from MCP servers

A connected MCP server that declares the skills extension adds its skills to the catalog as `server:skill`. Their instructions are fetched and verified when the model activates them, and a skill that declares `allowed-tools` needs your approval first. See [mcp-client.md](mcp-client.md).
