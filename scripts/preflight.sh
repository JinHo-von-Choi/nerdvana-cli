#!/usr/bin/env bash
# Run what CI runs, on a clean checkout of HEAD, before anything is pushed or tagged.
#
# Usage: scripts/preflight.sh [quick|full|release]
#   quick    lint, types, docs, test collection baseline, import graph, pricing freshness (a few minutes)
#   full     quick plus the whole test suite on Python 3.11 with the locked dependencies, as Quality Gate does
#   release  full plus the suite on Python 3.12 and on freshly resolved dependencies (what the LSP workflow
#            installs), the LSP integration tests, a wheel build and a clean install of it
#
# Why a clean checkout under $HOME: a developer's tree has untracked files, extra packages and a data
# directory that make checks pass here and fail in CI. The Landlock tests also treat /tmp as always
# writable, so a checkout under /tmp fails them for a reason CI does not have.
set -euo pipefail

mode="${1:-full}"
case "$mode" in quick|full|release) ;; *) echo "usage: $0 [quick|full|release]" >&2; exit 2 ;; esac

root="$(git rev-parse --show-toplevel)"
cd "$root"
if [ -n "$(git status --porcelain)" ]; then
    echo "preflight: the working tree has uncommitted changes; commit them first, the checkout below is HEAD" >&2
    exit 1
fi

work="$(mktemp -d "$HOME/.preflight.XXXXXX")"
cleanup() { git -C "$root" worktree remove --force "$work/wt" >/dev/null 2>&1 || true; rm -rf "$work"; }
trap cleanup EXIT
git worktree add -q --detach "$work/wt" HEAD
cd "$work/wt"

step() { printf '\n== preflight: %s\n' "$*"; }

step "dependencies (locked, every extra)"
uv sync --locked --all-extras -q

step "ruff"
uv run ruff check nerdvana_cli/ tests/ scripts/
step "mypy"
uv run mypy nerdvana_cli/
step "documentation matches the code"
uv run python scripts/check_docs_consistency.py
step "test collection baseline"
uv run python scripts/check_test_collection.py
step "contract tests (layers, hygiene, public-repository rules)"
uv run pytest tests/contracts -q -o addopts=""
step "import graph"
uv run python scripts/check_import_graph.py --root nerdvana_cli
step "pricing freshness"
uv run python scripts/check_pricing_freshness.py

if [ "$mode" != "quick" ]; then
    step "tests, Python 3.11, locked dependencies"
    uv run pytest -m "not lsp_integration and not live" -q -o addopts=""
fi

if [ "$mode" = "release" ]; then
    step "tests, Python 3.12, locked dependencies"
    uv venv -q .venv312 --python 3.12
    UV_PROJECT_ENVIRONMENT=.venv312 uv sync --locked --all-extras -q
    UV_PROJECT_ENVIRONMENT=.venv312 uv run pytest -m "not lsp_integration and not live" -q -o addopts=""

    step "tests, Python 3.11, freshly resolved dependencies"
    uv venv -q .venvfresh --python 3.11
    uv pip install -q --python .venvfresh/bin/python -e ".[dev,mcp,acp,otel,all]"
    .venvfresh/bin/python -m pytest -m "not lsp_integration and not live" -q -o addopts=""

    step "LSP integration tests"
    if command -v pyright-langserver >/dev/null 2>&1 || [ -x .venv/bin/pyright-langserver ]; then
        PATH="$PWD/.venv/bin:$PATH" uv run --no-sync pytest -m lsp_integration -q -o addopts=""
    else
        uv pip install -q pyright
        PATH="$PWD/.venv/bin:$PATH" uv run --no-sync pytest -m lsp_integration -q -o addopts=""
    fi

    step "wheel build and clean install"
    rm -rf .venv312 .venvfresh
    uv build -q
    smoke="$work/smoke"
    uv venv -q "$smoke" --python 3.11
    uv pip install -q --python "$smoke/bin/python" dist/*.whl
    HOME="$work" NERDVANA_DATA_HOME="$work/data" "$smoke/bin/nerdvana" version
    HOME="$work" NERDVANA_DATA_HOME="$work/data" "$smoke/bin/nerdvana" --help >/dev/null
fi

printf '\nPREFLIGHT OK (%s) at %s\n' "$mode" "$(git rev-parse --short HEAD)"
