"""Token usage and USD cost aggregation — nerdvana cost.

작성자: 최진호
작성일: 2026-04-29
"""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Time-window parsing
# ---------------------------------------------------------------------------

_WINDOW_RE = re.compile(r"^(\d+)([dh])$", re.IGNORECASE)


def parse_since(since: str) -> datetime | None:
    """Convert a ``since`` string to a UTC cutoff datetime.

    Accepted formats: ``Nd`` (days), ``Nh`` (hours), ``all`` (no limit).
    Returns ``None`` when *since* is ``"all"``.

    Raises ``ValueError`` for unrecognised formats.
    """
    if since.lower() == "all":
        return None
    m = _WINDOW_RE.match(since)
    if not m:
        raise ValueError(
            f"Unrecognised --since value: '{since}'. "
            "Use e.g. 7d, 30d, 24h, or all."
        )
    n, unit = int(m.group(1)), m.group(2).lower()
    delta = timedelta(days=n) if unit == "d" else timedelta(hours=n)
    return datetime.now(UTC) - delta


# ---------------------------------------------------------------------------
# Data loading from analytics.sqlite
# ---------------------------------------------------------------------------

def _analytics_db_path() -> Path:
    """Mirror the path logic from analytics.py without importing the module."""
    import os
    nerdvana_home = os.environ.get("NERDVANA_DATA_HOME", "").strip()
    base = Path(nerdvana_home).expanduser() if nerdvana_home else Path.home() / ".nerdvana"
    return base / "analytics.sqlite"


def load_usage_rows(
    db_path: Path,
    cutoff:  datetime | None,
    by:      str,
) -> list[dict[str, Any]]:
    """Query ``tool_calls`` and return per-(provider, model) or per-provider aggregates.

    Args:
        db_path: Path to ``analytics.sqlite``.
        cutoff:  UTC cutoff datetime; ``None`` means no time filter.
        by:      ``"provider"`` or ``"model"``.

    Returns:
        List of dicts with keys: provider, model, input_tokens, output_tokens, cost_usd.
        Empty list when the DB does not exist or has no matching rows.
    """
    if not db_path.exists():
        return []

    if by == "provider":
        select_cols = "COALESCE(provider, '(unknown)') AS provider, '' AS model"
        group_by    = "provider"
    else:
        select_cols = "COALESCE(provider, '(unknown)') AS provider, COALESCE(model, '(unknown)') AS model"
        group_by    = "provider, model"

    try:
        conn = sqlite3.connect(str(db_path), check_same_thread=False, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            has_api = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='api_calls'").fetchone() is not None
            merged  = _merge_usage(
                _aggregate(conn, _API_CALLS_SQL, select_cols, group_by, cutoff, has_api) if has_api else [],
                _aggregate(conn, _TOOL_CALLS_SQL, select_cols, group_by, cutoff, has_api),
            )
        finally:
            conn.close()
    except (sqlite3.Error, OSError):
        return []
    return sorted(merged, key=lambda r: (-r["cost_usd"], -r["input_tokens"]))


# Per-request usage the provider reported. Sessions recorded before it existed only have
# per-tool-call figures, so those are read for the sessions with no per-request rows.
_API_CALLS_SQL = """
    SELECT {select_cols},
           SUM(input_tokens)       AS input_tokens,
           SUM(output_tokens)      AS output_tokens,
           SUM(cache_read_tokens)  AS cache_read_tokens,
           SUM(cache_write_tokens) AS cache_write_tokens,
           SUM(cost_usd)           AS cost_usd
    FROM api_calls
    {where}
    GROUP BY {group_by}
"""
_TOOL_CALLS_SQL = """
    SELECT {select_cols},
           SUM(input_tokens)  AS input_tokens,
           SUM(output_tokens) AS output_tokens,
           0                  AS cache_read_tokens,
           0                  AS cache_write_tokens,
           SUM(cost_usd)      AS cost_usd
    FROM tool_calls
    {where}
    GROUP BY {group_by}
"""
_LEGACY_ONLY = "session_id IS NULL OR session_id NOT IN (SELECT DISTINCT session_id FROM api_calls WHERE session_id IS NOT NULL)"


def _aggregate(
    conn:        sqlite3.Connection,
    template:    str,
    select_cols: str,
    group_by:    str,
    cutoff:      datetime | None,
    has_api:     bool,
) -> list[dict[str, Any]]:
    """Run one usage query; with per-request rows present, tool-call rows only fill in sessions that have none."""
    api     = template is _API_CALLS_SQL
    clauses = []
    params: list[Any] = []
    if cutoff is not None:
        clauses.append("ts >= ?" if api else "start_ts >= ?")
        params.append(cutoff.isoformat())
    if not api and has_api:
        clauses.append(f"({_LEGACY_ONLY})")
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    try:
        rows = conn.execute(template.format(select_cols=select_cols, where=where, group_by=group_by), params).fetchall()
    except sqlite3.OperationalError:
        return []
    return [
        {
            "provider":           row["provider"],
            "model":              row["model"],
            "input_tokens":       int(row["input_tokens"]       or 0),
            "output_tokens":      int(row["output_tokens"]      or 0),
            "cache_read_tokens":  int(row["cache_read_tokens"]  or 0),
            "cache_write_tokens": int(row["cache_write_tokens"] or 0),
            "cost_usd":           float(row["cost_usd"]         or 0.0),
        }
        for row in rows
    ]


def _merge_usage(*groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Add up rows that share a provider and model across the query results."""
    merged: dict[tuple[str, str], dict[str, Any]] = {}
    for group in groups:
        for row in group:
            key = (row["provider"], row["model"])
            if key not in merged:
                merged[key] = dict(row)
                continue
            for field in ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens", "cost_usd"):
                merged[key][field] += row[field]
    return list(merged.values())


# ---------------------------------------------------------------------------
# Pricing status classification
# ---------------------------------------------------------------------------

def _pricing_status(provider: str, model: str) -> str:
    """Return a status tag for the given provider/model combination.

    ``"ok"``     — pricing entry exists and has non-zero rates.
    ``"tbd"``    — entry exists but both rates are explicitly zero.
    ``"unknown"``— entry is absent from pricing.yml.
    """
    from nerdvana_cli.core.analytics import PricingTable

    pt   = PricingTable()
    info = pt._prices.get(provider.lower(), {}).get(model.lower(), {})  # noqa: SLF001
    if not info:
        return "unknown"
    if info.get("input_per_1m", 0.0) == 0.0 and info.get("output_per_1m", 0.0) == 0.0:
        return "tbd"
    return "ok"


# ---------------------------------------------------------------------------
# Main aggregation helper
# ---------------------------------------------------------------------------

def build_cost_report(
    since:   str = "7d",
    by:      str = "provider",
    db_path: Path | None = None,
) -> dict[str, Any]:
    """Produce the cost report dict consumed by both table and JSON outputs.

    Returns a dict with keys:
        ``rows``           — per-group detail rows (sorted by cost desc).
        ``total_input``    — aggregate input token count.
        ``total_output``   — aggregate output token count.
        ``total_cache_read`` / ``total_cache_write`` — tokens served from or written to the prompt cache.
        ``total_cost_usd`` — aggregate USD cost.
        ``warning_count``  — number of rows whose pricing is unknown or TBD.
        ``since``          — the raw --since argument.
        ``by``             — the raw --by argument.
        ``cutoff_iso``     — ISO8601 cutoff string or "all".
        ``generated_at``   — ISO8601 timestamp of report generation.
    """
    try:
        cutoff = parse_since(since)
    except ValueError as exc:
        return {
            "error": str(exc),
            "rows":  [],
            "total_input":    0,
            "total_output":   0,
            "total_cache_read":  0,
            "total_cache_write": 0,
            "total_cost_usd": 0.0,
            "warning_count":  0,
            "since":          since,
            "by":             by,
            "cutoff_iso":     "invalid",
            "generated_at":   datetime.now(UTC).isoformat(),
        }

    path  = db_path or _analytics_db_path()
    raw   = load_usage_rows(path, cutoff, by)

    rows: list[dict[str, Any]] = [
        {**r, "status": "ok" if by == "provider" else _pricing_status(r["provider"], r["model"])}
        for r in raw
    ]
    warning_count = sum(1 for row in rows if row["status"] != "ok")

    return {
        "rows":           rows,
        "total_input":    sum(r["input_tokens"] for r in rows),
        "total_output":   sum(r["output_tokens"] for r in rows),
        "total_cache_read":  sum(r["cache_read_tokens"] for r in rows),
        "total_cache_write": sum(r["cache_write_tokens"] for r in rows),
        "total_cost_usd": sum(r["cost_usd"] for r in rows),
        "warning_count":  warning_count,
        "since":          since,
        "by":             by,
        "cutoff_iso":     cutoff.isoformat() if cutoff else "all",
        "generated_at":   datetime.now(UTC).isoformat(),
    }


# ---------------------------------------------------------------------------
# Rich table renderer
# ---------------------------------------------------------------------------

def _fmt_tokens(n: int) -> str:
    """Format an integer token count with thousands separator."""
    return f"{n:,}"


def _fmt_cost(usd: float, status: str) -> str:
    if status == "tbd":
        return "n/a (pricing TBD)"
    if status == "unknown":
        return "n/a (pricing TBD)"
    return f"${usd:.4g}"


def render_cost_table(report: dict[str, Any]) -> None:
    """Render the cost report as a Rich table to stdout."""
    from rich.console import Console
    from rich.table import Table

    console = Console()

    if "error" in report:
        console.print(f"[red]Error: {report['error']}[/red]")
        return

    rows = report["rows"]
    if not rows:
        console.print("[dim]no usage data[/dim]")
        return

    table = Table(show_header=True, header_style="bold")
    table.add_column("Provider",     style="cyan")
    table.add_column("Model",        style="")
    table.add_column("Input",        justify="right")
    table.add_column("Output",       justify="right")
    table.add_column("Cache read",   justify="right")
    table.add_column("Cache write",  justify="right")
    table.add_column("Cost (USD)",   justify="right")

    for row in rows:
        cost_str = _fmt_cost(row["cost_usd"], row["status"])
        table.add_row(
            row["provider"],
            row["model"],
            _fmt_tokens(row["input_tokens"]),
            _fmt_tokens(row["output_tokens"]),
            _fmt_tokens(row["cache_read_tokens"]),
            _fmt_tokens(row["cache_write_tokens"]),
            cost_str,
        )

    # Totals row
    total_cost_str = f"${report['total_cost_usd']:.4g}"
    table.add_row(
        "[bold]TOTAL[/bold]",
        "",
        f"[bold]{_fmt_tokens(report['total_input'])}[/bold]",
        f"[bold]{_fmt_tokens(report['total_output'])}[/bold]",
        f"[bold]{_fmt_tokens(report['total_cache_read'])}[/bold]",
        f"[bold]{_fmt_tokens(report['total_cache_write'])}[/bold]",
        f"[bold]{total_cost_str}[/bold]",
        end_section=False,
    )

    console.print(table)

    if report["warning_count"] > 0:
        console.print(
            f"[yellow]{report['warning_count']} model(s) have no pricing data "
            f"(cost_usd=0 for those rows).[/yellow]"
        )


# ---------------------------------------------------------------------------
# Typer entry point
# ---------------------------------------------------------------------------

def cost_command(since: str, json_output: bool, by: str) -> None:
    """Run nerdvana cost logic. Called from main.py."""
    if by not in ("provider", "model"):
        import typer
        from rich.console import Console
        Console(stderr=True).print(
            f"[red]Error: --by must be 'provider' or 'model', got '{by}'.[/red]"
        )
        raise typer.Exit(1)

    report = build_cost_report(since=since, by=by)

    if json_output:
        import sys
        print(json.dumps(report, indent=2), file=sys.stdout)
        return

    render_cost_table(report)
