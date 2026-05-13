# Contributing to NerdVana CLI

Author: 최진호
Created: 2026-04-29

---

## Environment bootstrap

Requirements: Python >= 3.11, git

Install [uv](https://docs.astral.sh/uv/getting-started/installation/) if not already present:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Then bootstrap the project:

```bash
git clone https://github.com/JinHo-von-Choi/nerdvana-cli.git
cd nerdvana-cli
uv sync --extra dev --extra mcp
pre-commit install
```

Python 3.11 or 3.12 is recommended (matches `pyproject.toml` constraints).

---

## Local quality gate

Run all three checks before opening a PR:

```bash
uv run ruff check nerdvana_cli/ tests/
uv run mypy nerdvana_cli/ --ignore-missing-imports
uv run pytest -m "not lsp_integration and not live" -q
```

All three commands must exit with code 0.

---

## Optional gates

**LSP integration tests** — requires `pyright` and `typescript-language-server` on PATH:

```bash
uv run pytest -m lsp_integration
```

**Live smoke tests** — requires provider API keys set as environment variables.
See [docs/security.md](docs/security.md) for the secrets policy and
[docs/testing-live.md](docs/testing-live.md) for the full provider matrix,
single-provider run recipes, and cost-aware execution guidance:

```bash
uv run pytest -m live
```

---

## New provider checklist

Follow the procedure template in `docs/plans/2026-04-29-add-providers-kimi-qwen.md`.
Files that require changes:

- `nerdvana_cli/providers/base.py` — add enum value, update provider dict, update `detect_provider`
- `nerdvana_cli/providers/factory.py` — wire new provider class
- `nerdvana_cli/providers/pricing.yml` — add token pricing entry
- `tests/test_<provider>_provider.py` — unit tests covering detect, build, stream, error paths
- `README.md` — add row to the Supported Providers table
- `CHANGELOG.md` — add entry under Unreleased
- `docs/configuration.md` — document provider-specific env vars and options
- `nerdvana.yml.example` — add commented example block

---

## Commit conventions

Use [Conventional Commits](https://www.conventionalcommits.org/):

```
feat: add Kimi provider with streaming support
fix: handle 429 retry for Groq provider
chore: bump ruff to 0.11.9
```

Accepted types: `feat`, `fix`, `chore`, `ci`, `docs`, `test`, `refactor`

Do not add `Co-Authored-By` lines to commit messages.

---

## PR gates

All of the following must be green before requesting review:

- Quality Gate workflow (`ruff`, `mypy`, `pytest`, dependency resolve)
- LSP CI workflow (required only when LSP-related files are modified)
- At least one reviewer approval
