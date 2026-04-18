"""Observability command handlers — /health and /dashboard."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from nerdvana_cli.core.analytics import AnalyticsReader

if TYPE_CHECKING:
    from nerdvana_cli.ui.app import NerdvanaApp


async def handle_health(app: NerdvanaApp, args: str) -> None:
    """Handle /health [--days N] [--json] — 7-day tool call health summary."""


    # Parse args: --days N and --json
    days      = 7
    as_json   = False
    tokens    = args.split() if args else []
    idx       = 0
    while idx < len(tokens):
        tok = tokens[idx]
        if tok == "--json":
            as_json = True
        elif tok == "--days" and idx + 1 < len(tokens):
            try:
                days = int(tokens[idx + 1])
                idx += 1
            except ValueError:
                pass
        idx += 1

    reader  = AnalyticsReader()
    summary = reader.summary(days=days)

    if as_json:
        raw = json.dumps(summary, indent=2)
        app._add_chat_message(f"[dim]{raw}[/dim]", raw_text=raw)
        return

    calls    = summary["total_calls"]
    tokens_n = summary["total_tokens"]
    cost     = summary["total_cost_usd"]
    failures = summary["top_failures"]

    lines = [
        f"[bold]Health ({days}d)[/bold]",
        f"  Calls:   {calls:,}",
        f"  Tokens:  {tokens_n:,}",
        f"  Cost:    ${cost:.4f}",
    ]
    if failures:
        lines.append("  Top failures:")
        for f in failures:
            lines.append(f"    {f['tool']}: {f['count']}")
    else:
        lines.append("  Top failures: none")

    app._add_chat_message("\n".join(lines))


async def handle_dashboard(app: NerdvanaApp, args: str) -> None:
    """Handle /dashboard — toggle the observability dashboard tab."""
    try:
        from nerdvana_cli.ui.dashboard_tab import DashboardTab
        app.query_one("#dashboard-tab", DashboardTab).toggle()
    except Exception:  # noqa: BLE001
        app._add_chat_message("[dim]Dashboard not available.[/dim]")
