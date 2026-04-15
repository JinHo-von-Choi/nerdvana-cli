"""Built-in hooks for session lifecycle."""

from __future__ import annotations

import re

from nerdvana_cli.core.hooks import HookContext, HookResult


def session_start_context_injection(ctx: HookContext) -> HookResult:
    """Inject neutral environment context into the session_start system_prompt.

    Returns a HookResult.system_prompt_append containing only nerdvana-cli
    own information: tool name list, session config summary. NIRNA.md is
    loaded by build_system_prompt directly and is NOT duplicated here.

    User-specific instructions (memory systems, project conventions, etc.)
    must be added via user hooks under ~/.nerdvana/hooks/ or
    <cwd>/.nerdvana/hooks/. See docs/hooks.md.
    """
    parts: list[str] = []

    if ctx.tools:
        tool_names = [t.name for t in ctx.tools if hasattr(t, "name")]
        if tool_names:
            parts.append(f"Available tools: {', '.join(tool_names)}")

    if ctx.settings:
        s = ctx.settings
        parts.append(
            f"Session config: provider={s.model.provider}, model={s.model.model}, "
            f"max_context={s.session.max_context_tokens}, max_turns={s.session.max_turns}"
        )

    if not parts:
        return HookResult()

    return HookResult(system_prompt_append="\n\n".join(parts))


def context_limit_recovery(ctx: HookContext) -> HookResult:
    """Auto-recovery hook for max_tokens stop.

    Registered on HookEvent.AFTER_API_CALL. Injects a continuation message
    asking the model to resume from where it left off, based on the last
    user message in the history.
    """
    if ctx.stop_reason != "max_tokens":
        return HookResult()

    last_user_content = ""
    if ctx.messages:
        for msg in reversed(ctx.messages):
            role = str(getattr(msg, "role", ""))
            if role == "user" or role.endswith("user"):
                content = getattr(msg, "content", "")
                if isinstance(content, str) and content:
                    last_user_content = content
                    break

    continuation = (
        "Context limit reached. Continue from where you left off. "
        + (f"Last request: {last_user_content[:200]}" if last_user_content else "")
    )
    return HookResult(
        inject_messages=[{"role": "user", "content": continuation}]
    )


def json_parse_recovery(ctx: HookContext) -> HookResult:
    """Inject a correction message when a tool result had a JSON parse error.

    Registered on HookEvent.AFTER_TOOL. The caller must populate
    ctx.extra["json_error"] with the error description.
    """
    json_error = ctx.extra.get("json_error") if ctx.extra else None
    if not json_error:
        return HookResult()

    msg = (
        f"JSON parse failure (tool: {ctx.tool_name or 'unknown'}). "
        f"Error: {json_error}\n"
        "Please retry with valid JSON format."
    )
    return HookResult(
        inject_messages=[{"role": "user", "content": msg}]
    )


_INCOMPLETE_PATTERNS = re.compile(
    r"(TODO|FIXME|#\s*구현\s*필요|#\s*미구현|#\s*needs?\s*implementation|NotImplemented|raise\s+NotImplementedError)",
    re.IGNORECASE,
)


def ralph_loop_check(ctx: HookContext) -> HookResult:
    """Scan the last assistant message for unfinished markers on end_turn.

    Registered on HookEvent.AFTER_API_CALL. If TODO/FIXME/NotImplemented
    patterns are present, inject a message asking the agent to finish them.
    """
    if ctx.stop_reason != "end_turn":
        return HookResult()

    last_content = ""
    if ctx.messages:
        for msg in reversed(ctx.messages):
            if str(getattr(msg, "role", "")) == "assistant":
                last_content = getattr(msg, "content", "") or ""
                break

    if not isinstance(last_content, str):
        return HookResult()

    matches = _INCOMPLETE_PATTERNS.findall(last_content)
    if not matches:
        return HookResult()

    unique = list(dict.fromkeys(matches))[:5]
    msg = (
        f"Incomplete items found: {', '.join(unique)}\n"
        "Complete all TODOs and unimplemented items before responding."
    )
    return HookResult(
        inject_messages=[{"role": "user", "content": msg}]
    )
