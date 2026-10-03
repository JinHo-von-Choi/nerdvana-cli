# Masking secrets in tool output

What a command prints goes to the model, into the session transcript and to the provider. An `env`
listing, a `.env` shown by `cat` or a token in a curl trace would carry credentials along. Before the output
of `Bash`, `Parism`, `WebFetch`, `WebSearch` and MCP tools reaches the model, values that look like secrets are replaced
with `[REDACTED]`, and the result ends with a note saying how many values were replaced. The output of a failing
verification command (see [goals.md](goals.md)) is masked the same way.

## What is masked

- The exact values of environment variables whose names look like credentials (`*_API_KEY`, `*_TOKEN`, `*SECRET*`,
  `*PASSWORD*`, database URLs and the like, the same names the `Bash` tool already withholds from commands), and the
  API key in use, wherever they appear in the output. Values shorter than 8 characters are not masked.
- Text shaped like a credential: `sk-` keys of OpenAI, Anthropic and OpenRouter, GitHub tokens, AWS access key ids, Google API keys,
  Slack tokens, JWTs, PEM private keys and `Authorization: Bearer` headers.
- Assignments whose name says the value is a credential (`password = ...`, `"token": "..."`, `API_KEY: ...`): only
  the value is replaced.

## What is not

- File tools (`FileRead`, `Grep`, `FileEdit` and the symbol tools). The model has to read and edit files exactly
  as they are; replacing a value it then copies back would damage the file. A secret in a file reaches the model when
  the file is read; keep credentials out of the files the agent works on.
- Secrets the process does not hold and that match no pattern.
- Arguments the model itself writes into a tool call.

This is a mitigation, not a boundary.

## Keeping a credential out of the command

Masking works on output after the fact. A token a command needs for one web service can instead stay out of
its environment: with `sandbox.network: allowlist`, `secrets.proxy_credentials` names an environment variable
per domain and the egress proxy adds it to the plain HTTP requests for that domain. See
[sandbox.md](sandbox.md) for the setup and its limits (TLS tunnels cannot carry an added header).

## Configuration

```yaml
session:
  mask_secrets: true            # false turns masking off
  mask_extra_patterns:          # regular expressions whose matches are replaced as well
    - 'ACME-[0-9]{6}'
```

Each replaced value is counted as `secret_masked` in the `signals` of a run result.
