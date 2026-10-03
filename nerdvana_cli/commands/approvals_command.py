"""nerdvana approvals: rules suggested by the permission questions you keep approving.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

import json
import sys
from typing import Any

from nerdvana_cli.core.approvals import MIN_APPROVALS, Suggestion, suggest_rules


def build_report(rows: list[dict[str, Any]], existing: list[str], min_approvals: int = MIN_APPROVALS) -> dict[str, Any]:
    """The suggestions and the numbers they rest on."""
    suggestions = suggest_rules(rows, min_approvals, existing)
    return {
        "questions":   sum(r["allowed"] + r["denied"] for r in rows),
        "min_approvals": min_approvals,
        "suggestions": [{"rule": s.rule, "approvals": s.approvals} for s in suggestions],
    }


def render(report: dict[str, Any]) -> str:
    """The report as text, ending with a block to paste into the configuration."""
    suggestions = [Suggestion(s["rule"], s["approvals"]) for s in report["suggestions"]]
    lines = [f"{report['questions']} permission question(s) answered in the period."]
    if not suggestions:
        lines.append(f"No exact call was approved {report['min_approvals']} times or more without ever being refused.")
        return "\n".join(lines)
    lines += ["", "Approved every time, at least that often:"]
    lines += [f"  {s.approvals:>3}x  {s.rule}" for s in suggestions]
    lines += ["", "To stop being asked about these exact calls, add to the configuration:", "", "permissions:", "  always_allow:"]
    lines += [f"    - \"{s.rule}\"" for s in suggestions]
    return "\n".join(lines)


def approvals_command(since: int, min_approvals: int, json_output: bool) -> None:
    """Print rule suggestions from the recorded answers. Nothing is written to the configuration."""
    from nerdvana_cli.core.analytics import AnalyticsReader
    from nerdvana_cli.core.settings import NerdvanaSettings

    try:
        existing = list(NerdvanaSettings.load().permissions.always_allow)
    except Exception:  # noqa: BLE001 - a broken config must not hide the history
        existing = []
    report = build_report(AnalyticsReader().approvals(days=since), existing, min_approvals)
    if json_output:
        print(json.dumps(report, indent=2), file=sys.stdout)
        return
    print(render(report))
