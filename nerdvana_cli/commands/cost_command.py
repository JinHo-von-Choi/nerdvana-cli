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

    try:
        conn = sqlite3.connect(str(db_path), check_same_thread=False, timeout=10)
        conn.row_factory = sqlite3.Row

        if by == "provider":
            group_cols  = "COALESCE(provider, '(unknown)') AS provider"
            group_by    = "provider"
            select_cols = f"{group_cols}, '' AS model"
        else:
            select_cols = (
                "COALESCE(provider, '(unknown)') AS provider, "
                "COALESCE(model, '(unknown)') AS model"
            )
            group_by = "provider, model"

        params: list[Any] = []
        where   = ""
        if cutoff is not None:
            where  = "WHERE start_ts >= ?"
            params.append(cutoff.isoformat())

        sql = f"""
            SELECT {select_cols},
                   SUM(input_tokens)  AS input_tokens,
                   SUM(output_tokens) AS output_tokens,
                   SUM(cost_usd)      AS cost_usd
            FROM tool_calls
            {where}
            GROUP BY {group_by}
            ORDER BY cost_usd DESC, input_tokens DESC
        """
        rows = conn.execute(sql, params).fetchall()
        conn.close()
    except (sqlite3.Error, OSError):
        return []

    return [
        {
            "provider":      row["provider"],
            "model":         row["model"],
            "input_tokens":  int(row["input_tokens"]  or 0),
            "output_tokens": int(row["output_tokens"] or 0),
            "cost_usd":      float(row["cost_usd"]    or 0.0),
        }
        for row in rows
    ]


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
    if info.get("input_per_1k", 0.0) == 0.0 and info.get("output_per_1k", 0.0) == 0.0:
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
            "total_cost_usd": 0.0,
            "warning_count":  0,
            "since":          since,
            "by":             by,
            "cutoff_iso":     "invalid",
            "generated_at":   datetime.now(UTC).isoformat(),
        }

    path  = db_path or _analytics_db_path()
    raw   = load_usage_rows(path, cutoff, by)

    rows:          list[dict[str, Any]] = []
    total_input    = 0
    total_output   = 0
    total_cost_usd = 0.0
    warning_count  = 0

    for r in raw:
        provider = r["provider"]
        model    = r["model"]

        status = "ok" if by == "provider" else _pricing_status(provider, model)
        if status != "ok":
            warning_count += 1

        total_input    += r["input_tokens"]
        total_output   += r["output_tokens"]
        total_cost_usd += r["cost_usd"]

        rows.append({
            "provider":      provider,
            "model":         model,
            "input_tokens":  r["input_tokens"],
            "output_tokens": r["output_tokens"],
            "cost_usd":      r["cost_usd"],
            "status":        status,
        })

    return {
        "rows":           rows,
        "total_input":    total_input,
        "total_output":   total_output,
        "total_cost_usd": total_cost_usd,
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
    table.add_column("Cost (USD)",   justify="right")

    for row in rows:
        cost_str = _fmt_cost(row["cost_usd"], row["status"])
        table.add_row(
            row["provider"],
            row["model"],
            _fmt_tokens(row["input_tokens"]),
            _fmt_tokens(row["output_tokens"]),
            cost_str,
        )

    # Totals row
    total_cost_str = f"${report['total_cost_usd']:.4g}"
    table.add_row(
        "[bold]TOTAL[/bold]",
        "",
        f"[bold]{_fmt_tokens(report['total_input'])}[/bold]",
        f"[bold]{_fmt_tokens(report['total_output'])}[/bold]",
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
