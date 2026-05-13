#!/usr/bin/env bash
# fetch_lsp_bench_fixtures.sh — download a pinned copy of a permissively-licensed
# Python project to serve as the medium-tier LSP benchmark fixture.
#
# Usage:
#   bash scripts/fetch_lsp_bench_fixtures.sh [TARGET_DIR]
#
# Defaults:
#   TARGET_DIR = tests/lsp/fixtures-medium
#
# The chosen project: pallets/click @ 8.1.8 (BSD-3-Clause)
#   - ~55 .py files after excluding docs/tests
#   - Stable, well-maintained, zero transitive submodules
#   - Commit SHA pinned for reproducibility
#
# Idempotency:
#   If TARGET_DIR/.fixture-version already contains the expected SHA, exit 0.
#
# Author: 최진호
# Created: 2026-05-13

set -euo pipefail

# ---------------------------------------------------------------------------
# Configuration — update PINNED_SHA when upgrading the fixture
# ---------------------------------------------------------------------------
REPO_URL="https://github.com/pallets/click.git"
PINNED_SHA="4cf9d57aaecc2c11a1a8b48a98cb74e2e0b6a1ee"
FIXTURE_VERSION_FILE=".fixture-version"

# ---------------------------------------------------------------------------
# Argument handling
# ---------------------------------------------------------------------------
TARGET_DIR="${1:-tests/lsp/fixtures-medium}"

# ---------------------------------------------------------------------------
# Idempotency check
# ---------------------------------------------------------------------------
if [[ -d "${TARGET_DIR}" && -f "${TARGET_DIR}/${FIXTURE_VERSION_FILE}" ]]; then
    stored_sha="$(cat "${TARGET_DIR}/${FIXTURE_VERSION_FILE}")"
    if [[ "${stored_sha}" == "${PINNED_SHA}" ]]; then
        echo "[fetch-fixtures] already at ${PINNED_SHA:0:12} — skipping download"
        exit 0
    else
        echo "[fetch-fixtures] version mismatch (have ${stored_sha:0:12}, want ${PINNED_SHA:0:12}) — re-fetching"
        rm -rf "${TARGET_DIR}"
    fi
fi

# ---------------------------------------------------------------------------
# Dependency check
# ---------------------------------------------------------------------------
if ! command -v git &>/dev/null; then
    echo "error: git is required" >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# Shallow clone at the pinned SHA
# ---------------------------------------------------------------------------
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "${TMP_DIR}"' EXIT

echo "[fetch-fixtures] cloning ${REPO_URL} at ${PINNED_SHA:0:12}..."

git clone \
    --depth 1 \
    --no-tags \
    --quiet \
    "${REPO_URL}" \
    "${TMP_DIR}/repo"

# Checkout the exact SHA (the shallow clone gives HEAD; we verify it matches)
actual_sha="$(git -C "${TMP_DIR}/repo" rev-parse HEAD)"
if [[ "${actual_sha}" != "${PINNED_SHA}" ]]; then
    # Shallow clone with --depth 1 fetches the default branch tip.
    # If the tag tip differs from our SHA, fetch the precise commit.
    echo "[fetch-fixtures] HEAD mismatch — fetching exact commit..."
    git -C "${TMP_DIR}/repo" fetch --depth 1 origin "${PINNED_SHA}" 2>/dev/null \
        || { echo "error: could not fetch SHA ${PINNED_SHA}" >&2; exit 1; }
    git -C "${TMP_DIR}/repo" checkout "${PINNED_SHA}" --quiet
fi

# ---------------------------------------------------------------------------
# Copy to target, stripping .git/
# ---------------------------------------------------------------------------
mkdir -p "$(dirname "${TARGET_DIR}")"
cp -r "${TMP_DIR}/repo" "${TARGET_DIR}"
rm -rf "${TARGET_DIR}/.git"

# Write version sentinel
printf '%s\n' "${PINNED_SHA}" > "${TARGET_DIR}/${FIXTURE_VERSION_FILE}"

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
py_files="$(find "${TARGET_DIR}" -name '*.py' | wc -l | tr -d ' ')"
total_loc=0
while IFS= read -r -d '' f; do
    lines="$(wc -l < "${f}" 2>/dev/null || echo 0)"
    total_loc=$(( total_loc + lines ))
done < <(find "${TARGET_DIR}" -name '*.py' -print0)

echo "[fetch] cloned ${REPO_URL} @ ${PINNED_SHA:0:12} to ${TARGET_DIR} (${py_files} .py files, ${total_loc} LOC)"
