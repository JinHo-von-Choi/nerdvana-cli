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

## Changing the system prompt, tool schemas or loop behaviour

A change to what every request carries (the system prompt, the tool list and their descriptions, the
compaction or masking rules, the default settings of the loop) can shift the success rate and the cost
without any test failing. Besides the quality gate:

1. Run `uv run pytest tests/core/test_prompt_prefix_stability.py`: the start of the request must stay
   byte-identical from turn to turn, or the provider's prompt cache is lost.
2. Run the benchmark before and after with at least 4 attempts per task and compare the two files:

   ```bash
   uv run python scripts/bench_agent.py benchmarks/tasks --attempts 4 --yes --isolate --out before.jsonl
   # apply the change, then
   uv run python scripts/bench_agent.py benchmarks/tasks --attempts 4 --yes --isolate --out after.jsonl
   uv run python scripts/bench_compare.py before.jsonl after.jsonl
   ```

   `docs/benchmarks/agent-success-rate.md` explains the options and how to read the interval. A change that
   cannot be told apart in pass rate but costs more input tokens is a regression.
3. Watch the `cache_miss` signal in the run result: it counts requests whose cache read fell to zero with
   nothing in the loop to explain it.

---

## Before you push or release

CI runs on a clean checkout with the locked dependencies, and a second workflow installs freshly
resolved dependencies. A tree that passes on your machine can still fail there (an optional extra that
CI does not install, a newer typer, a checkout under `/tmp`). So never push on the strength of a local
run alone:

```bash
git config core.hooksPath .githooks      # once per clone: a push to main runs the quick preflight
scripts/preflight.sh quick               # ruff, mypy, docs, collection baseline, import graph, pricing (about 1 minute)
scripts/preflight.sh full                # plus the whole suite on Python 3.11, locked, as Quality Gate does
scripts/preflight.sh release             # plus Python 3.12, freshly resolved dependencies, LSP tests, wheel install
```

The preflight checks out `HEAD` into a temporary worktree under `$HOME`, so uncommitted changes are not
tested: commit first. To release, run `scripts/release.sh X.Y.Z` and nothing else: it runs the release
preflight, bumps the version, pushes `main` without the tag, waits until GitHub's checks on that commit are
green, and only then creates and pushes the tag. A tag is never pushed ahead of green checks.

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

- `nerdvana_cli/providers/base.py`: add enum value, update provider dict, update `detect_provider`
- `nerdvana_cli/providers/factory.py`: wire new provider class
- `nerdvana_cli/providers/pricing.yml`: add token pricing entry
- `tests/providers/test_<provider>_provider.py`: unit tests covering detect, build, stream, error paths
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
