"""/context and nerdvana context: where the context window goes.

Author: 최진호
Date:   2026-10-03

Both front ends print the same breakdown (``core/context_report.py``). The slash command reads the live
conversation of the running agent loop. The CLI command rebuilds a loop for a stored session: the
conversation comes from the transcript, the system prompt and the tool declarations from the current
settings and tools.
"""

from __future__ import annotations

import dataclasses
import json
import os
import sys
from typing import TYPE_CHECKING, Any

from rich.markup import escape

from nerdvana_cli.core.context_report import ContextReport, advice, build_report, render
from nerdvana_cli.core.tool_index import ToolIndex

if TYPE_CHECKING:
    from nerdvana_cli.core.agent_loop import AgentLoop
    from nerdvana_cli.ui.app import NerdvanaApp

# What the loop appends to the system prompt after build_system_prompt: attribute and the label it is shown under.
_LOOP_EXTRAS: tuple[tuple[str, str], ...] = (
    ("_sticky_session_context", "session context (workspace snapshot, memory hint, hook output)"),
    ("_role_prompt",            "agent role"),
    ("_active_skill",           "active skill"),
)

STORED_NOTE = (
    "Rebuilt from a stored session: the system prompt and the tool declarations are those of the current settings "
    "and tools, the session context of a live run is not included, and the transcript keeps only the first 500 "
    "characters of each tool result, so the tool results are understated."
)


def _declared_tools(loop: AgentLoop) -> tuple[list[Any], int]:
    """(tools the next request declares, number deferred behind ToolSearch)."""
    visible  = [t for t in loop.registry.all_tools() if loop.policy.is_visible(t.name) and t.name != "ToolSearch"]
    session  = loop.settings.session
    index    = getattr(loop, "_tool_index", None) or ToolIndex.build(visible, session.defer_tools, session.defer_tools_threshold)
    declared = index.declared(visible)
    deferred = len(visible) - len(declared)
    search   = loop.registry.get("ToolSearch")
    if index.deferred and search is not None:
        declared.append(search)
    return declared, deferred


def report_for_loop(loop: AgentLoop) -> ContextReport:
    """The context breakdown of the next request *loop* would make."""
    tools, deferred = _declared_tools(loop)
    extras          = {label: str(getattr(loop, attr, "") or "") for attr, label in _LOOP_EXTRAS}
    return build_report(loop.build_system_prompt(), tools, loop.state.messages, loop.settings.session, deferred, extras)


def report_json(report: ContextReport) -> dict[str, Any]:
    """The report as plain data, with its totals and advice."""
    return {
        **dataclasses.asdict(report),
        "total":            report.total,
        "system_tokens":    report.system_tokens,
        "tool_tokens":      report.tool_tokens,
        "message_tokens":   report.conversation_tokens,
        "advice":           advice(report),
    }


async def handle_context(app: NerdvanaApp, args: str) -> None:
    """Handle /context: the breakdown with no argument or ``usage``, the profile commands otherwise."""
    from nerdvana_cli.commands import profile_commands

    token = args.strip().lower()
    if token not in ("", "usage"):
        await profile_commands.handle_context(app, args)
        return
    if app._agent_loop is None:
        app._add_chat_message("[dim]No active session.[/dim]")
    else:
        text = render(report_for_loop(app._agent_loop))
        app._add_chat_message(escape(text), raw_text=text)
    if not token:
        await profile_commands.handle_context(app, "")


def _stored_loop(session_id: str) -> AgentLoop:
    """An agent loop holding the conversation of the stored session *session_id*."""
    from nerdvana_cli.cli.bootstrap import ExecutionProfile, build_agent_loop
    from nerdvana_cli.core.session import SessionStorage
    from nerdvana_cli.core.settings import NerdvanaSettings

    settings     = NerdvanaSettings.load()
    settings.cwd = os.getcwd()
    loop         = build_agent_loop(settings, ExecutionProfile(session=SessionStorage(session_id=session_id, persist=False)))
    loop.restore_history()
    return loop


def context_command(session_id: str, top: int, json_output: bool) -> int:
    """Print the breakdown for a stored session (the latest when *session_id* is empty); the exit code."""
    from nerdvana_cli.core.session import SessionStorage, resume_session_id

    wanted = session_id.strip() or SessionStorage.get_last_session() or ""
    sid    = resume_session_id(wanted)
    if sid is None or not os.path.exists(SessionStorage(session_id=sid, persist=False).file_path):
        print(f"Error: no stored session {wanted!r}.", file=sys.stderr)
        return 2
    report = report_for_loop(_stored_loop(sid))
    if json_output:
        print(json.dumps({"session": sid, **report_json(report)}, indent=2))
    else:
        print(f"Session {sid}\n{STORED_NOTE}\n\n{render(report, top)}")
    return 0
