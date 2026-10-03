"""Built-in hooks for session lifecycle."""

from __future__ import annotations

import hashlib
import os
import re
from typing import Any

from nerdvana_cli.core.context.nirnamd import is_within_root, read_rule_file
from nerdvana_cli.core.hooks.hooks import HookContext, HookResult


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


def session_start_memory_hint(ctx: HookContext) -> HookResult:
    """Inject a memory count hint at session start.

    Appends a one-line hint to the system prompt: how many project memories
    exist and that ListMemories can enumerate them.  Body is never injected
    automatically — only the count, to avoid token over-consumption.
    """
    if ctx.settings is None:
        return HookResult()

    cwd = getattr(ctx.settings, "cwd", None)
    if not cwd:
        return HookResult()

    try:
        from nerdvana_cli.core.context.memories import MemoriesManager
        mgr  = MemoriesManager(cwd)
        hint = mgr.session_start_hint()
    except Exception:  # noqa: BLE001
        return HookResult()

    if not hint:
        return HookResult()

    return HookResult(system_prompt_append=hint)


_INCOMPLETE_PATTERNS = re.compile(
    r"(TODO|FIXME|#\s*구현\s*필요|#\s*미구현|#\s*needs?\s*implementation|NotImplemented|raise\s+NotImplementedError)",
    re.IGNORECASE,
)


def ralph_loop_check(ctx: HookContext) -> HookResult:
    """Scan the current assistant response for unfinished markers on end_turn.

    Registered on HookEvent.AFTER_API_CALL. If TODO/FIXME/NotImplemented
    patterns are present, inject a message asking the agent to finish them.

    Uses ``ctx.extra["asst_text"]`` (the response text for this specific turn)
    so that prior assistant messages in the history are not re-scanned on
    subsequent turns, preventing spurious re-injection loops.
    """
    if ctx.stop_reason != "end_turn":
        return HookResult()

    # Prefer the current-turn text passed explicitly by the agent loop.
    # Fall back to scanning history only when asst_text is absent (e.g. tests
    # that do not populate extra).
    asst_text_from_extra: str | None = (ctx.extra or {}).get("asst_text")
    if asst_text_from_extra is not None:
        last_content = asst_text_from_extra
    else:
        last_content = ""
        if ctx.messages:
            for msg in reversed(ctx.messages):
                if str(getattr(msg, "role", "")) == "assistant":
                    last_content = getattr(msg, "content", "") or ""
                    break

    if not isinstance(last_content, str):
        return HookResult()

    # Nothing to scan when the turn produced no text.
    if not last_content:
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


RULE_FILENAMES:    tuple[str, ...] = ("NIRNA.md", "AGENTS.md", "CLAUDE.md")
RULE_FILE_TOOLS:   frozenset[str]  = frozenset({"FileRead", "FileEdit", "FileWrite"})
RULE_BUDGET_BYTES: int             = 32 * 1024


class DirectoryRuleInjector:
    """Inject directory-scoped rule files the first time a file below them is touched.

    Registered on HookEvent.AFTER_TOOL. For FileRead, FileEdit and FileWrite on a
    file inside a subdirectory of the project root, every NIRNA.md, AGENTS.md and
    CLAUDE.md between the file's directory and the project root (root excluded,
    the root files are part of the system prompt) is delivered once as a
    user-role message, nearest directory first. Content is deduplicated by
    sha256 and the total delivered bytes are capped per session.

    HookContext carries no session id, so the state lives on the instance, which
    the agent loop creates once per session and clears through reset().
    """

    def __init__(self, budget_bytes: int = RULE_BUDGET_BYTES) -> None:
        self._budget_bytes     = budget_bytes
        self._seen_hashes:     set[str] = set()
        self._used_bytes       = 0
        self._truncation_noted = False

    def reset(self) -> None:
        """Forget what was injected, for a conversation that starts over."""
        self._seen_hashes.clear()
        self._used_bytes       = 0
        self._truncation_noted = False

    def handle(self, ctx: HookContext) -> HookResult | None:
        """AFTER_TOOL handler; returns the rule messages to inject, if any."""
        if ctx.tool_name not in RULE_FILE_TOOLS or getattr(ctx.tool_result, "is_error", False):
            return None
        root = getattr(ctx.settings, "cwd", None)
        raw  = ctx.tool_input.get("path") if ctx.tool_input else None
        if not root or not isinstance(raw, str) or not raw:
            return None

        resolved_root = os.path.realpath(root)
        target        = os.path.realpath(os.path.join(resolved_root, raw))
        if not is_within_root(target, resolved_root):
            return None

        messages: list[dict[str, Any]] = []
        for directory in self._directories_below_root(os.path.dirname(target), resolved_root):
            for name in RULE_FILENAMES:
                self._collect(os.path.join(directory, name), resolved_root, messages)
        return HookResult(inject_messages=messages) if messages else None

    @staticmethod
    def _directories_below_root(start: str, root: str) -> list[str]:
        """Directories from *start* up to, excluding, *root*, nearest first."""
        directories: list[str] = []
        current = start
        while current != root and is_within_root(current, root):
            directories.append(current)
            parent = os.path.dirname(current)
            if parent == current:
                break
            current = parent
        return directories

    def _collect(self, path: str, root: str, messages: list[dict[str, Any]]) -> None:
        content = read_rule_file(path, root)
        if not content:
            return
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        if digest in self._seen_hashes:
            return
        size     = len(content.encode("utf-8"))
        relative = os.path.relpath(path, root)
        if self._used_bytes + size > self._budget_bytes:
            if not self._truncation_noted:
                self._truncation_noted = True
                messages.append({
                    "role":    "user",
                    "content": (
                        f"Directory rule budget of {self._budget_bytes} bytes reached; "
                        f"{relative} and later rule files were not injected. "
                        "Read them directly if they matter for the current task."
                    ),
                })
            return
        self._seen_hashes.add(digest)
        self._used_bytes += size
        messages.append({
            "role":    "user",
            "content": f"Directory rules from {relative}:\n\n{content}",
        })
