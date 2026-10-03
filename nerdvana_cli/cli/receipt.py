"""The receipt of a run: what changed, what checked it, under which limits and what it cost.

Author: 최진호
Date:   2026-10-03

A result that says "done" is a claim. The receipt puts the evidence next to it, in one object that a
script or another tool can read: the files the edit tools changed, how the goal's verification command
ended, the sandbox policy the commands ran under, the cost per agent and the counts of things that went
wrong on the way. It is assembled from what the run measured; nothing in it is written by the model.
"""

from __future__ import annotations

from typing import Any

RECEIPT_VERSION = 1


def build_receipt(loop: Any, verification: dict[str, Any] | None, breakdown: dict[str, dict[str, float]]) -> dict[str, Any]:
    """The receipt of the session of *loop*; *breakdown* is the analytics view of cost per agent."""
    sandbox = loop.settings.sandbox
    signals = loop.signal_summary()
    return {
        "receipt_version": RECEIPT_VERSION,
        "files_changed":   dict(sorted(loop.tool_executor.edited.items())),
        "verification":    verification,
        "sandbox": {
            "mode": sandbox.mode, "network": sandbox.network, "project_writable": sandbox.project_writable,
            "edit_scope": sandbox.edit_scope,
        },
        "cost_by_agent": breakdown,
        "problems": {
            name: signals[name]
            for name in ("cas_rejected", "new_diagnostics", "repeat_refused", "out_of_scope", "secret_masked", "verify_failed", "escalated")
            if name in signals
        },
    }
