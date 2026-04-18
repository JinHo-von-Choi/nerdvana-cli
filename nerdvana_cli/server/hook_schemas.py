"""Hook JSON schema models — Claude Code / Codex / VSCode hook protocol.

Claude Code hook spec:
  https://docs.anthropic.com/en/docs/claude-code/hooks

Payload (stdin) varies by hook type; response (stdout) always follows:
  {
    "hookSpecificOutput": {
      "permissionDecision": "approve" | "deny" | "ask",   # pre-tool-use only
      "additionalContext":  "<string>"                     # prompt-submit / post-tool-use
    }
  }

작성자: 최진호
작성일: 2026-04-18
"""

from __future__ import annotations

from typing import Any, Literal

from typing_extensions import TypedDict

# ---------------------------------------------------------------------------
# Inbound payload typedefs
# ---------------------------------------------------------------------------

class PreToolUsePayload(TypedDict, total=False):
    """Payload delivered to ``pre-tool-use`` hooks.

    Fields mirror the Claude Code hook specification; all optional so we
    can tolerate partial payloads from Codex / VSCode variants.
    """

    hook_event_name: str          # "PreToolUse"
    tool_name:       str          # e.g. "Bash", "Edit", "Read"
    tool_input:      dict[str, Any]
    session_id:      str
    transcript_path: str


class PostToolUsePayload(TypedDict, total=False):
    """Payload delivered to ``post-tool-use`` hooks."""

    hook_event_name: str          # "PostToolUse"
    tool_name:       str
    tool_input:      dict[str, Any]
    tool_response:   dict[str, Any]
    session_id:      str
    transcript_path: str


class PromptSubmitPayload(TypedDict, total=False):
    """Payload delivered to ``prompt-submit`` (UserPromptSubmit) hooks."""

    hook_event_name: str          # "UserPromptSubmit"
    prompt:          str          # raw user message text
    session_id:      str
    transcript_path: str


# ---------------------------------------------------------------------------
# Outbound response typedefs
# ---------------------------------------------------------------------------

PermissionDecision = Literal["approve", "deny", "ask"]


class HookSpecificOutput(TypedDict, total=False):
    """Inner ``hookSpecificOutput`` object."""

    permissionDecision: PermissionDecision   # pre-tool-use
    additionalContext:  str                  # all hook types


class HookResponse(TypedDict):
    """Top-level hook response written to stdout."""

    hookSpecificOutput: HookSpecificOutput


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

HOOK_NAMES = frozenset(
    {
        "pre-tool-use",
        "post-tool-use",
        "prompt-submit",
    }
)


def make_response(
    *,
    permission_decision: PermissionDecision | None = None,
    additional_context:  str = "",
) -> HookResponse:
    """Construct a well-formed :class:`HookResponse` dict.

    Parameters
    ----------
    permission_decision:
        Only meaningful for ``pre-tool-use`` hooks.  Omit for others.
    additional_context:
        Text injected into the conversation as additional context.
    """
    inner: HookSpecificOutput = {}
    if permission_decision is not None:
        inner["permissionDecision"] = permission_decision
    if additional_context:
        inner["additionalContext"] = additional_context
    return {"hookSpecificOutput": inner}
