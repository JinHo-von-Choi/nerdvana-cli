"""bench_lsp_diff.py — compare two bench_lsp.py JSON outputs and print a delta table.

Usage::

    python scripts/bench_lsp_diff.py <baseline.json> <current.json> \\
        [--threshold-warn PCT] [--threshold-fail PCT]

Default thresholds (per the regression policy in docs/benchmarks/lsp-baseline-2026-05-13.md):
  --threshold-warn  25   (print WARN when delta >= 25%)
  --threshold-fail  50   (exit code 1 when delta >= 50%)

Output (stdout): markdown table.

Exit codes:
  0 — all metrics ok or warn-only
  1 — at least one metric FAIL
  2 — input file invalid / unreadable

Handles missing keys gracefully: if either file carries
``"status": "no-lsp-server-on-path"`` the comparison is skipped and the
script exits 0 with an explanatory note.

Author: 최진호
Created: 2026-05-13
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Metrics we compare (dot-separated path into the JSON payload)
# ---------------------------------------------------------------------------
METRICS: list[tuple[str, str]] = [
    ("cold_open_ms",              "cold_open_ms"),
    ("diagnostics.p95_ms",        "diagnostics / p95_ms"),
    ("diagnostics.mean_ms",       "diagnostics / mean_ms"),
    ("goto_definition.p95_ms",    "goto_definition / p95_ms"),
    ("find_references.p95_ms",    "find_references / p95_ms"),
]


# ---------------------------------------------------------------------------
# JSON helpers
# ---------------------------------------------------------------------------

def _load_json(path: str) -> dict[str, Any]:
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        print(f"error: cannot read {path}: {exc}", file=sys.stderr)
        sys.exit(2)
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        print(f"error: {path} is not valid JSON: {exc}", file=sys.stderr)
        sys.exit(2)
    if not isinstance(data, dict):
        print(f"error: {path} must be a JSON object", file=sys.stderr)
        sys.exit(2)
    return data


def _get_nested(data: dict[str, Any], dotpath: str) -> float | None:
    """Return a nested float value by dot-separated key path, or None."""
    parts = dotpath.split(".")
    node: Any = data
    for part in parts:
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    if isinstance(node, (int, float)):
        return float(node)
    return None


# ---------------------------------------------------------------------------
# Comparison logic (importable for unit tests)
# ---------------------------------------------------------------------------

class MetricRow:
    """Holds the comparison result for a single metric."""

    __slots__ = ("label", "baseline", "current", "delta_pct", "status")

    def __init__(
        self,
        label: str,
        baseline: float | None,
        current: float | None,
        warn_pct: float,
        fail_pct: float,
    ) -> None:
        self.label    = label
        self.baseline = baseline
        self.current  = current

        if baseline is None or current is None:
            self.delta_pct: float | None = None
            self.status = "skip"
        elif baseline == 0.0:
            self.delta_pct = None
            self.status    = "skip"
        else:
            pct            = (current - baseline) / baseline * 100.0
            self.delta_pct = round(pct, 1)
            if pct >= fail_pct:
                self.status = "FAIL"
            elif pct >= warn_pct:
                self.status = "WARN"
            else:
                self.status = "ok"

    @property
    def any_fail(self) -> bool:
        return self.status == "FAIL"


def compare(
    baseline: dict[str, Any],
    current: dict[str, Any],
    warn_pct: float = 25.0,
    fail_pct: float = 50.0,
) -> list[MetricRow]:
    """Return a MetricRow for every tracked metric."""
    rows: list[MetricRow] = []
    for dotpath, label in METRICS:
        b_val = _get_nested(baseline, dotpath)
        c_val = _get_nested(current, dotpath)
        rows.append(MetricRow(label, b_val, c_val, warn_pct, fail_pct))
    return rows


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def _fmt_ms(v: float | None) -> str:
    return f"{v:.2f}" if v is not None else "n/a"


def _fmt_pct(v: float | None) -> str:
    if v is None:
        return "n/a"
    sign = "+" if v >= 0 else ""
    return f"{sign}{v:.1f}%"


def render_table(rows: list[MetricRow]) -> str:
    lines: list[str] = [
        "| metric | baseline_ms | current_ms | delta_pct | status |",
        "|-|-|-|-|-|",
    ]
    for row in rows:
        lines.append(
            f"| {row.label} "
            f"| {_fmt_ms(row.baseline)} "
            f"| {_fmt_ms(row.current)} "
            f"| {_fmt_pct(row.delta_pct)} "
            f"| {row.status} |"
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("baseline", help="Path to baseline bench_lsp JSON output.")
    parser.add_argument("current",  help="Path to current bench_lsp JSON output.")
    parser.add_argument(
        "--threshold-warn",
        type=float,
        default=25.0,
        metavar="PCT",
        help="Warn when a metric grows by this %% (default: 25).",
    )
    parser.add_argument(
        "--threshold-fail",
        type=float,
        default=50.0,
        metavar="PCT",
        help="Fail (exit 1) when a metric grows by this %% (default: 50).",
    )
    args = parser.parse_args(argv)

    base_data = _load_json(args.baseline)
    curr_data = _load_json(args.current)

    # Graceful skip when either side has no LSP server
    base_status = base_data.get("status", "")
    curr_status = curr_data.get("status", "")
    if "no-lsp-server-on-path" in (base_status, curr_status):
        which = args.baseline if base_status == "no-lsp-server-on-path" else args.current
        print(f"skipped: no LSP server (from {which})")
        sys.exit(0)

    rows = compare(base_data, curr_data, args.threshold_warn, args.threshold_fail)
    print(render_table(rows))

    if any(r.any_fail for r in rows):
        sys.exit(1)


if __name__ == "__main__":
    main()
