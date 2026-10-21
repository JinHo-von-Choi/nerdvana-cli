# Custom Commands

A markdown file under `~/.nerdvana/commands/` or `<project>/.nerdvana/commands/` becomes a slash command in the REPL.

| File | Command |
|-|-|
| `review.md` | `/review` |
| `git/commit.md` | `/git:commit` |

Typing the command sends the file's text as the prompt.

| Placeholder | Replaced by |
|-|-|
| `$ARGUMENTS` | Everything typed after the command |
| `$1` to `$9` | The words typed after the command (quote a word that contains spaces) |

Without a placeholder the arguments are appended to the text. An optional `description:` in YAML frontmatter shows in the command menu.

```markdown
---
description: Review the staged changes
---
Review the staged changes in this repository. Focus on $ARGUMENTS.
```

A project file replaces a global one of the same name. Built-in commands and skills always win over both.

`nerdvana import claude` and `nerdvana import codex` copy existing Claude Code (`.claude/commands`) and Codex (`~/.codex/prompts`) commands into `.nerdvana/commands` without overwriting; they show a plan until `--write` is given.
