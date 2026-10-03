"""Counting what goes wrong in a run, by kind.

Author: 최진호
Date:   2026-10-03

A failed run says it failed; it does not say why. These counters turn a run into a small
distribution (how many edits were refused as stale, how many calls were blocked as repeats,
how many times the language server found a new error after an edit, and so on), which is what
decides what to fix first. They come from the run itself, so they cost no model call.

Tool results are classified from the messages the executor and the file tools write, so a change
to one of those messages has to change the matching prefix here; the tests pin each of them.
"""

from __future__ import annotations

from collections import Counter

# Signal names, as they appear in a run result and in the benchmark summary.
REPEAT_REFUSED      = "repeat_refused"
REPEAT_WARNED       = "repeat_warned"
INVALID_INPUT       = "invalid_input"
PERMISSION_POLICY   = "permission_denied_policy"
PERMISSION_USER     = "permission_denied_user"
HOOK_BLOCKED        = "hook_blocked"
VALIDATION_ERROR    = "validation_error"
TOOL_EXCEPTION      = "tool_exception"
CAS_REJECTED        = "cas_rejected"
NEW_DIAGNOSTICS     = "new_diagnostics"
SANDBOX_DENIED      = "sandbox_denied"
TOOL_ERROR          = "tool_error"
TODO_NUDGE          = "todo_nudge"
PROVIDER_RETRY      = "provider_retry"
PROVIDER_FALLBACK   = "provider_fallback"
COMPACTION          = "compaction"
OBSERVATIONS_MASKED = "observations_masked"
WRAP_UP             = "wrap_up"
VERIFY_FAILED       = "verify_failed"
TOOL_NOT_LOADED     = "tool_not_loaded"
OUT_OF_SCOPE        = "out_of_scope"
SECRET_MASKED       = "secret_masked"
OUT_OF_GOAL_SCOPE   = "out_of_goal_scope"
ESCALATED           = "escalated"
NO_PROGRESS         = "no_progress"

# (text the result starts with or contains, signal), checked in order for error results.
_ERROR_PREFIXES = (
    ("Refused: ",                   REPEAT_REFUSED),
    ("Invalid tool input",          INVALID_INPUT),
    ("Permission denied by user",   PERMISSION_USER),
    ("Permission denied: ",         PERMISSION_POLICY),
    ("Blocked by hook",             HOOK_BLOCKED),
    ("Validation error",            VALIDATION_ERROR),
    ("Tool execution error",        TOOL_EXCEPTION),
)
_STALE_PHRASES = ("has not been read in this session", "changed since it was last read")


def classify_result(content: str, is_error: bool, *, shell_confined: bool = False) -> list[str]:
    """The signals one tool result carries.

    A refused or failed call carries exactly one error signal. A result that succeeded can still
    carry notes: a repeat warning, or errors the language server found after an edit.
    ``shell_confined`` says the command ran under the sandbox, which makes a "Permission denied"
    in its output a sandbox refusal (an approximation: a file's own mode bits say the same words).
    """
    found: list[str] = []
    if "[Note: this exact call has now been made" in content:
        found.append(REPEAT_WARNED)
    if "New errors reported by the language server after this edit" in content:
        found.append(NEW_DIAGNOSTICS)
    if not is_error:
        if shell_confined and "Permission denied" in content:
            found.append(SANDBOX_DENIED)
        return found
    for prefix, signal in _ERROR_PREFIXES:
        if content.startswith(prefix):
            return [*found, signal]
    if content.startswith("Outside this agent's edit scope"):
        return [*found, OUT_OF_SCOPE]
    if "is not loaded yet. Call ToolSearch" in content:
        return [*found, TOOL_NOT_LOADED]
    if any(phrase in content for phrase in _STALE_PHRASES):
        return [*found, CAS_REJECTED]
    if shell_confined and "Permission denied" in content:
        return [*found, SANDBOX_DENIED]
    return [*found, TOOL_ERROR]


def escalation_reason(counts: dict[str, int], thresholds: dict[str, int]) -> str:
    """The first signal (in the order of *thresholds*) that reached its limit, as text; empty when none did."""
    for name, limit in thresholds.items():
        if limit > 0 and counts.get(name, 0) >= limit:
            return f"{name} x{counts[name]}"
    return ""


def merge(*counters: Counter[str]) -> dict[str, int]:
    """Add up several counters into a plain, name-sorted dict without zero entries."""
    total: Counter[str] = Counter()
    for counter in counters:
        total.update(counter)
    return {name: count for name, count in sorted(total.items()) if count}
