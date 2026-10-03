"""nerdvana approvals: rules suggested by the permission questions you keep approving.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

import json
import sys
from dataclasses import asdict
from typing import Any

from nerdvana_cli.core.approvals import MIN_APPROVALS, Suggestion, compare_verdicts, suggest_rules


def build_report(
    rows:            list[dict[str, Any]],
    existing:        list[str],
    min_approvals:   int = MIN_APPROVALS,
    classifier_rows: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """The suggestions and the numbers they rest on, and the classifier's shadow verdicts against what happened."""
    suggestions = suggest_rules(rows, min_approvals, existing)
    comparison  = compare_verdicts(classifier_rows or [])
    return {
        "questions":   sum(r["allowed"] + r["denied"] for r in rows),
        "min_approvals": min_approvals,
        "suggestions": [{"rule": s.rule, "approvals": s.approvals} for s in suggestions],
        "classifier":  {**asdict(comparison), "agreement": comparison.agreement},
    }


def render_comparison(report: dict[str, Any]) -> list[str]:
    """The lines that set the classifier's shadow verdicts against the answers, empty when there are none."""
    data = report["classifier"]
    if not data["judged"] and not data["errors"]:
        return []
    rate  = data["agreement"]
    lines = ["", f"Action classifier (shadow): {data['judged']} call(s) judged, {data['errors']} failed (would have been asked)."]
    if rate is None:
        lines.append("  You answered none of the judged calls, so there is no agreement to measure yet.")
    else:
        lines.append(f"  Agreement with your answers: {data['agreed']} of {data['answered']} ({rate:.0%}).")
    lines += [
        f"  Would have asked, you allowed:   {data['would_ask_allowed']}",
        f"  Would have denied, you allowed:  {data['would_deny_allowed']}",
        f"  Would have allowed, you refused: {data['would_allow_refused']}",
        f"  Ran unasked, would have been asked or denied: {data['interrupts']} of {data['unasked']}",
    ]
    return lines


def render(report: dict[str, Any]) -> str:
    """The report as text, ending with a block to paste into the configuration."""
    suggestions = [Suggestion(s["rule"], s["approvals"]) for s in report["suggestions"]]
    lines = [f"{report['questions']} permission question(s) answered in the period."]
    if not suggestions:
        lines.append(f"No exact call was approved {report['min_approvals']} times or more without ever being refused.")
        return "\n".join(lines + render_comparison(report))
    lines += ["", "Approved every time, at least that often:"]
    lines += [f"  {s.approvals:>3}x  {s.rule}" for s in suggestions]
    lines += ["", "To stop being asked about these exact calls, add to the configuration:", "", "permissions:", "  always_allow:"]
    lines += [f"    - \"{s.rule}\"" for s in suggestions]
    return "\n".join(lines + render_comparison(report))


def approvals_command(since: int, min_approvals: int, json_output: bool) -> None:
    """Print rule suggestions from the recorded answers. Nothing is written to the configuration."""
    from nerdvana_cli.core.config.settings import NerdvanaSettings
    from nerdvana_cli.core.telemetry.analytics import AnalyticsReader

    try:
        existing = list(NerdvanaSettings.load().permissions.always_allow)
    except Exception:  # noqa: BLE001 - a broken config must not hide the history
        existing = []
    reader = AnalyticsReader()
    report = build_report(reader.approvals(days=since), existing, min_approvals, reader.classifier_outcomes(days=since))
    if json_output:
        print(json.dumps(report, indent=2), file=sys.stdout)
        return
    print(render(report))
