# Plugins

A plugin is one folder, or a git repository, that installs skills, slash commands, MCP servers and shell hooks together.

```bash
nerdvana plugin install ./review-kit                 # a local folder
nerdvana plugin install https://github.com/me/kit    # a git repository
nerdvana plugin list
nerdvana plugin remove review-kit
```

## Layout

```
review-kit/
├── nerdvana-plugin.yml     name, version, description
├── skills/<skill>/SKILL.md Agent Skills
├── commands/*.md           slash commands
├── mcp.json                {"mcpServers": {...}}
└── hooks.yml               {"hooks": [...]} shell command hooks
```

```yaml
# nerdvana-plugin.yml
name: review-kit        # lowercase letters, digits and hyphens
version: 1.0.0
description: Review skills and commands
```

Every part is optional except the manifest.

## What installing does

| Part | Goes to | Notes |
|-|-|-|
| `skills/<skill>/` | `~/.nerdvana/skills/<skill>/` | A skill of the same name is not replaced without `--force` |
| `commands/` | `~/.nerdvana/commands/<plugin>/` | A command `pr.md` becomes `/review-kit:pr` |
| `mcp.json` | merged into `~/.nerdvana/mcp.json` | A server name that is already configured stops the install |
| `hooks.yml` | appended to `~/.nerdvana/hooks.yml` | Only with `--allow-hooks`: hooks run shell commands on every session |

The install is recorded in `~/.nerdvana/plugins/<name>.json`. `nerdvana plugin remove` uses that record to take back exactly what the plugin added; entries you added yourself stay. An install that fails part-way takes back what it had already placed.
