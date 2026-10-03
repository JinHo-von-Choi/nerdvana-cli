"""The managed policy check of ``nerdvana doctor``.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

from nerdvana_cli.cli.commands.doctor_result import CheckResult


def check_managed_policy() -> CheckResult:
    """Report the managed settings files that applied and every one that could not be read."""
    from nerdvana_cli.core.config.managed_policy import scan_managed_policy

    policy, problems = scan_managed_policy()
    if problems:
        return CheckResult("managed_policy", "fail", "; ".join(problems))
    if not policy.active:
        return CheckResult("managed_policy", "skip", "no managed settings files")
    return CheckResult("managed_policy", "ok", f"{len(policy.files)} file(s) applied: {', '.join(item.path for item in policy.files)}")
