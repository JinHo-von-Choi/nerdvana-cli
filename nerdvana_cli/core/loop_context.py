"""What the agent loop adds around the model's own history: the provider form, reports and session context.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

import logging
from typing import Any

from nerdvana_cli.core.context_snapshot import collect_snapshot, format_snapshot
from nerdvana_cli.core.hooks import HookContext, HookEngine, HookEvent
from nerdvana_cli.core.settings import NerdvanaSettings
from nerdvana_cli.core.todos import describe, load_todos, open_items
from nerdvana_cli.types import Message, Role

logger = logging.getLogger(__name__)

# Longest background task output quoted in a completion notice.
_BACKGROUND_REPORT_CHARS = 4_000


def provider_messages(messages: list[Message]) -> list[dict[str, Any]]:
    """*messages* in the form every provider adapter takes."""
    out: list[dict[str, Any]] = []
    for msg in messages:
        if msg.role == Role.USER:
            out.append({"role": "user", "content": msg.content})
        elif msg.role == Role.ASSISTANT:
            d = {"role": "assistant", "content": msg.content, **({"tool_uses": msg.tool_uses} if msg.tool_uses else {})}
            if msg.provider_blocks:
                d["provider_blocks"] = msg.provider_blocks
            out.append(d)
        elif msg.role == Role.TOOL:
            out.append({"role": "tool", "content": msg.content, "tool_use_id": msg.tool_use_id or "", "is_error": msg.is_error})
    return out


def background_reports(task_registry: Any) -> list[Message]:
    """One message per background task that finished since the model last looked."""
    if task_registry is None or not hasattr(task_registry, "drain_unreported"):
        return []
    reports: list[Message] = []
    for task in task_registry.drain_unreported():
        body = task.output if task.output else (task.error or "")
        if len(body) > _BACKGROUND_REPORT_CHARS:
            body = body[:_BACKGROUND_REPORT_CHARS] + f"\n... [cut; TaskGet {task.id} returns the full output]"
        reports.append(Message(
            role    = Role.USER,
            content = f"[Background task {task.id} {task.status}] {task.description}\n{body}",
        ))
    return reports


def open_todos_note(session_id: str) -> list[Message]:
    """After compaction, the open todo items of the session that the summary may have lost."""
    pending = open_items(load_todos(session_id))
    if not pending:
        return []
    return [Message(role=Role.USER, content=f"Open todo items (kept across compaction):\n{describe(pending)}")]


async def session_start_context(settings: NerdvanaSettings, hooks: HookEngine, tools: list[Any]) -> str:
    """What the session's requests carry after the system prompt: a workspace snapshot and SESSION_START output."""
    parts: list[str] = []
    try:
        snapshot = format_snapshot(await collect_snapshot(settings.cwd or "."))
        if snapshot.strip():
            parts.append(snapshot)
    except Exception as exc:  # noqa: BLE001
        logger.debug("context snapshot skipped: %s", exc)
    for result in hooks.fire(HookContext(event=HookEvent.SESSION_START, settings=settings, tools=tools)):
        if result.system_prompt_append:
            parts.append(result.system_prompt_append)
        parts.extend(str(message["content"]) for message in result.inject_messages if message.get("content"))
    return "\n\n".join(parts)
