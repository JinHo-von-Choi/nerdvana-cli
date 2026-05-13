# LSP Benchmark Baseline — 2026-05-13

Author: 최진호
Workflow: `scripts/bench_lsp.py`

This document records the first numerical baseline for LSP-driven operations
exposed by `core/lsp_client.py`. Future runs should be appended as new files
under `docs/benchmarks/lsp-<date>.md` and the regression check should compare
against the most recent prior entry.

## Methodology

```bash
uv run python scripts/bench_lsp.py \
    --root tests/lsp/fixtures/sample_python_project \
    --iterations 5 \
    --out docs/benchmarks/lsp-2026-05-13.json
```

Metrics captured per run:

- `cold_open_ms` — wall-clock for first `diagnostics()` call (one file).
- `diagnostics` — summary over the remaining files (n, mean, p50, p95, max).
- `goto_definition` / `find_references` — summary over `--iterations` repetitions.
- `available_tools` — list of LSP-backed tool classes the client exposes.
- `target` — concrete `{file, line, symbol}` the operations were driven against.

The harness is fail-soft:
- Exit code `2` and `status: no-lsp-server-on-path` when neither `pyright` nor
  `typescript-language-server` is on PATH (skip-friendly in CI).
- Exit code `0` with `status: lsp-error` and the error string captured in the
  payload when the server initialises but a request fails.

## Fixture matrix (initial)

| Tier | Path | Python files | Approx. symbols | Purpose |
|-|-|-|-|-|
| small | `tests/lsp/fixtures/sample_python_project` | 3 | 8 | smoke harness, CI-friendly |
| medium | _TBD — PR-9b_ | ~50 | ~10k | day-to-day editing scenarios |
| large | _TBD — PR-9b_ | ~500 | ~50k+ | scaling stress |

Medium and large tiers are tracked as TODO. They require either git submodule
or a `scripts/fetch_lsp_bench_fixtures.sh` downloader so the main repo
checkout stays small — see the D-9 design note.

## Baseline numbers

A reproducible baseline run requires `pyright` (or `pyright-langserver`) on
PATH. The dev environment used to land this PR did not have it installed, so
the first numerical row below is left blank. The next contributor with a
properly provisioned environment should run the command above and append the
result here.

| Date | Tier | cold_open_ms | diagnostics.p95 | goto.p95 | find_refs.p95 | LSP server |
|-|-|-|-|-|-|-|
| 2026-05-13 | small | _pending_ | _pending_ | _pending_ | _pending_ | pyright |

## Regression policy (proposed)

Once at least two baselines exist:

- Warn if any p95 grows > 25% over the prior baseline.
- Fail nightly CI if any p95 grows > 50%.
- The script can be wrapped into a comparator (`scripts/bench_lsp_diff.py`)
  that consumes two JSON outputs and prints a delta table — out of scope for
  PR-9a; tracked for PR-9c.

## Caching strategy notes (forward-looking)

Per the parent roadmap (D-9 in the analysis report), the harness exists to
inform a future caching decision in `core/lsp_client.py` /
`core/symbol_graph.py`. Candidates evaluated:

1. **LRU on in-memory request results** — quickest to add; loses state on
   process exit; good fit if cold cost is acceptable.
2. **TTL on disk (JSONL log)** — survives restarts; staleness tradeoff.
3. **Persistent SQLite index** — adds a dependency but enables sub-millisecond
   lookups for repeated `find_references` on hot symbols.

A separate ADR (`docs/adr/lsp-cache-strategy.md`) should pick one once two
real measurements (small + medium) are on file.
