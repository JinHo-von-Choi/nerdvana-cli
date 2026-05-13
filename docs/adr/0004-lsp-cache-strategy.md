# ADR 0004 — LSP Cache Strategy

작성자: 최진호
작성일: 2026-05-13
상태: Proposed

## Context

`nerdvana_cli/core/lsp_client.py` is invoked for every `find_references`,
`goto_definition`, and `diagnostics` call that a tool-use turn triggers.
Current measurements (small fixture — 3 files, 8 symbols) show the harness
exits with `status: no-lsp-server-on-path` in most CI environments, so no
numerical p95 baseline exists yet for `cold_open_ms` or `find_references.p95_ms`.

Two cost patterns motivate a caching layer:

1. Cold-open cost — the LSP server process must index the project before
   responding. On medium codebases (50 files, ~10 k symbols) this can reach
   several hundred milliseconds even with `pyright`'s incremental mode.
   Paying this cost every session restart erodes the interactive latency budget.

2. Repeated hot-symbol lookups — `find_references` on widely-used symbols
   (e.g. a base class or a shared utility function) is called many times
   within a session with identical inputs. Without caching, each call blocks
   an agent tool turn for the full round-trip.

Until the medium-tier fixture baseline exists (see `docs/benchmarks/lsp-baseline-2026-05-13.md`,
tier "medium — TBD — PR-9b"), any caching strategy risks being calibrated
against wrong numbers. The small fixture (8 symbols) is too cheap to reveal
whether cold-open or repeated-lookup dominates.

## Decision

Defer implementation until at least two real measurement points exist:
one from the small fixture and one from the medium fixture. Pre-document
three candidate strategies so the implementing PR can reference concrete
trade-offs rather than re-derive them.

### Candidate A — LRU in-memory cache

Cache `find_references` and `goto_definition` results in a bounded
`functools.lru_cache` or `collections.OrderedDict` keyed on
`(file_path, line, symbol)`.

Criteria:
- Implementation effort: minimal (< 30 LOC).
- Cold-open benefit: none — the LSP server still starts per session.
- Repeated-lookup benefit: eliminates round-trips for hot symbols within
  a session; typical speedup proportional to symbol reuse rate.
- Persistence: none — cache is lost on process exit.
- Staleness: bounded by process lifetime; safe in interactive sessions
  where file changes are visible to the LSP server anyway.
- Dependency cost: zero (stdlib only).

Best fit when: cold-open p95 is acceptable but repeated-lookup cost
(measured via `find_references.p95_ms` across iterations) is the dominant
bottleneck.

### Candidate B — JSONL on-disk cache with TTL

Append cache entries as JSONL records in a project-local file
(e.g. `.nerdvana/lsp_cache.jsonl`). On lookup, read the most recent entry
whose TTL has not expired.

Criteria:
- Implementation effort: moderate (~80 LOC including TTL eviction sweep).
- Cold-open benefit: partial — if the prior session wrote diagnostics,
  a subsequent session can warm-start from disk before the LSP server
  responds.
- Repeated-lookup benefit: same as Candidate A plus cross-session.
- Staleness: controlled by TTL. Short TTL (< 60 s) negates the
  cold-open benefit; long TTL (> 24 h) risks serving stale references
  after file edits.
- Dependency cost: zero (stdlib only); file size grows unboundedly
  without a compaction step.

Best fit when: cold-open p95 is the dominant cost and the session-restart
frequency is high (e.g. terminal multiplexer workflows that kill and
respawn the CLI repeatedly within the same working tree).

### Candidate C — SQLite persistent index

Store results in a project-local SQLite database
(`.nerdvana/lsp_cache.db`), indexed on `(file_path, symbol, mtime)`.
Invalidate entries when `mtime` of the source file changes.

Criteria:
- Implementation effort: high (~150 LOC including schema, mtime
  invalidation, and WAL mode setup).
- Cold-open benefit: strongest — diagnostics can be served from the
  index before the LSP server finishes its own cold index sweep.
- Repeated-lookup benefit: sub-millisecond after the first write.
- Staleness: mtime-based invalidation is precise but can miss in-memory
  editor saves that do not flush to disk immediately.
- Dependency cost: `sqlite3` is in stdlib; no external packages.
  However, concurrent access from multiple CLI instances requires
  WAL mode and retry logic.

Best fit when: both cold-open and repeated-lookup costs are material and
the project is large enough that LSP indexing time exceeds 500 ms.

## Decision Drivers

| Driver | Weight | Notes |
|-|-|-|
| cold_open_ms p95 target | high | Must be < 200 ms for interactive feel |
| restart frequency | medium | Determines value of cross-session persistence |
| dependency budget | low | All three candidates use stdlib only |
| implementation risk | medium | More complex = more edge-cases in invalidation |

The p95 target and restart frequency can only be quantified once the
medium-tier benchmark (`scripts/fetch_lsp_bench_fixtures.sh` + `bench_lsp.py`)
produces real numbers.

## Open Questions

1. Which project will serve as the medium-tier fixture? The fetch script
   pins `pallets/click` at SHA `a69cd5cb` but this should be validated
   against the ~50-file / ~10 k symbol requirement before the baseline run.
2. What TTL is appropriate for Candidate B in a fast-edit workflow where
   files change every few minutes? A TTL below 120 s may reduce hit-rate
   below useful levels.
3. At what symbol-count does the cache invalidation race (editor saves
   mid-lookup) become a measurable problem? This is unknown until the
   medium fixture has been benchmarked.
4. Should the cache be shared across `goto_definition` and
   `find_references`, or maintained separately (different staleness
   profiles)?

## Consequences

Choosing Candidate A prematurely:
- Fastest to ship; provides within-session speedup immediately.
- Provides no data to decide whether B or C is needed — the medium
  baseline must still be collected.

Choosing Candidate C prematurely:
- Highest implementation cost; risk of WAL/mtime bugs before we know
  whether the cold-open cost justifies the complexity.

Waiting (current decision):
- No user-visible latency improvement until the baseline confirms the
  problem is worth solving.
- Gives the medium-tier fixture time to be collected and vetted before
  the caching PR lands.

## Related

- `docs/benchmarks/lsp-baseline-2026-05-13.md` — small-tier baseline and
  regression policy; medium-tier row marked as pending.
- `scripts/bench_lsp.py` — benchmark harness that produces the JSON
  consumed by the regression checker.
- `scripts/bench_lsp_diff.py` — delta table tool; prints WARN/FAIL per
  metric against configurable thresholds.
- `scripts/fetch_lsp_bench_fixtures.sh` — fetch script for medium-tier
  fixture (pallets/click, pinned SHA).
