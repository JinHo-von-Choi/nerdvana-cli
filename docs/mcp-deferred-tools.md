# Deferred MCP tools

Every request to the model carries the full declaration of every tool: its name, description and
parameter schema. A few built-in tools cost a few thousand tokens. An MCP server can add dozens of
tools and tens of thousands of tokens, paid again on every request of the session.

When the MCP tools' declarations add up to more than `session.defer_tools_threshold` (3000 estimated
tokens), they are deferred:

- The system prompt lists them under "Deferred tools", one line each.
- A `ToolSearch` tool is declared. `ToolSearch` with `select:name1,name2` loads those tools; any other query
  searches their names (weighted more) and descriptions. The result shows the description and the parameters.
- A loaded tool is declared in full from the next request on and stays declared for the rest of the session.
- Calling a tool that is deferred and not loaded is refused with a message that says how to load it.

Built-in tools are always declared in full, and sub-agents (`Agent`, `Swarm`) declare every tool they are allowed.

```yaml
session:
  defer_tools: auto           # auto | always | never
  defer_tools_threshold: 3000
```

## Cost and caching

Loading a tool adds its declaration after the ones already declared, so the beginning of the request keeps
its place. Providers that cache the start of a request (see `model.prompt_caching`) still reread what
follows the new tool once. Within a prompt the list of deferred names stays as it was when the prompt began, so
the system prompt does not change mid-run; the next prompt lists only what is still unloaded.

Deferring pays off when a session uses a few of many tools. A session that ends up using most of them pays
for the search steps as well, so set `defer_tools: never` for it.
