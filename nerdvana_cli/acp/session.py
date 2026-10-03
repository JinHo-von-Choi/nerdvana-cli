"""One ACP session: an agent loop working in a directory, and what it does told to the editor as ACP updates.

Author: 최진호
Date:   2026-10-03

The loop is driven through its public surface only: ``run``, the confirm and thinking callbacks, the usage
listener, the hook engine and task cancellation. Everything bound for the editor goes through one ordered
outbox, so a message chunk, a tool call and a usage figure arrive in the order they happened even though some
of them come from synchronous hooks.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass
from typing import Any

from acp import (
    start_tool_call,
    update_agent_message_text,
    update_agent_thought_text,
    update_plan,
    update_tool_call,
    update_user_message_text,
)
from acp.exceptions import RequestError
from acp.helpers import update_available_commands
from acp.schema import Cost, PermissionOption, PromptResponse, StopReason, ToolCallUpdate, Usage, UsageUpdate

from nerdvana_cli.acp.mcp_servers import session_mcp_configs
from nerdvana_cli.acp.prompt_content import available_commands, expand_command, prompt_parts
from nerdvana_cli.acp.tool_mapping import (
    approval_key,
    plan_entries,
    result_content,
    text_content,
    tool_diff,
    tool_kind,
    tool_locations,
    tool_title,
)
from nerdvana_cli.cli.bootstrap import ExecutionProfile, build_agent_loop
from nerdvana_cli.cli.run_output import classify_chunk
from nerdvana_cli.core.agent_loop import AgentLoop
from nerdvana_cli.core.config.settings import NerdvanaSettings
from nerdvana_cli.core.context.user_commands import UserCommandLoader
from nerdvana_cli.core.delegation.task_state import TaskRegistry
from nerdvana_cli.core.hooks.hooks import HookContext, HookEvent
from nerdvana_cli.core.state.session import SessionStorage
from nerdvana_cli.mcp.config import McpServerConfig
from nerdvana_cli.mcp.manager import McpManager
from nerdvana_cli.types import Message, Role

logger = logging.getLogger(__name__)

# AgentLoop.last_stop -> ACP stop reason. A stop that is not listed ends the turn normally.
_STOP_REASONS: dict[str, StopReason] = {
    "completed":        "end_turn",
    "goal_unmet":       "end_turn",
    "max_turns":        "max_turn_requests",
    "max_tokens":       "max_tokens",
    "max_cost":         "refusal",
    "max_total_tokens": "refusal",
    "unpriced":         "refusal",
}

# Stops that end the prompt request with a JSON-RPC error instead of a stop reason.
_ERROR_STOPS = frozenset({"provider_error", "error"})

_PERMISSION_OPTIONS = [
    PermissionOption(option_id="allow_once",   name="Allow once",                             kind="allow_once"),
    PermissionOption(option_id="allow_always", name="Always allow this call in this session", kind="allow_always"),
    PermissionOption(option_id="reject_once",  name="Reject",                                 kind="reject_once"),
]


@dataclass
class _Call:
    """A tool call the model made and the editor has been told about."""

    tool_use_id: str
    name:        str
    tool_input:  dict[str, Any]
    started:     bool = False
    asked:       bool = False


class _ObservedStorage(SessionStorage):
    """Session transcript that also reports the tool calls of an assistant message before they run."""

    def __init__(self, on_tool_uses: Any, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._on_tool_uses = on_tool_uses

    def record_assistant_message(
        self,
        content:         str,
        tool_uses:       list[dict[str, Any]] | None = None,
        provider_blocks: list[dict[str, Any]] | None = None,
    ) -> None:
        if tool_uses:
            self._on_tool_uses(tool_uses)
        super().record_assistant_message(content, tool_uses, provider_blocks)


def _notice_class(chunk: str) -> str:
    """What to do with a system notice of the loop: ``drop`` a dim one, ``hold`` an error, ``show`` the rest."""
    tag = chunk.lstrip().split("]", 1)[0]
    if "dim" in tag:
        return "drop"
    return "hold" if "red" in tag else "show"


def _message_text(message: Message) -> str:
    """The text of a conversation message; the text blocks of one that carries images."""
    if isinstance(message.content, str):
        return message.content
    return "\n".join(str(block.get("text", "")) for block in message.content if block.get("type") == "text")


class AcpSession:
    """The agent loop of one ACP session and its channel to the editor."""

    def __init__(self, conn: Any, settings: NerdvanaSettings, session_id: str | None = None) -> None:
        self._conn            = conn
        self.settings         = settings
        self.cwd              = settings.cwd
        self._outbox:         asyncio.Queue[Any] = asyncio.Queue()
        self._pump_task:      asyncio.Task[None] | None = None
        self._turn:           asyncio.Task[StopReason] | None = None
        self._cancelled       = False
        self._calls:          dict[str, _Call] = {}
        self._decisions:      dict[tuple[str, str], bool] = {}
        self._confirm_lock    = asyncio.Lock()
        self._thought_seen    = ""
        self._permission_seq  = 0
        self._mcp:            McpManager | None = None
        self._commands        = UserCommandLoader(project_dir=self.cwd)
        self._storage         = _ObservedStorage(
            self._announce,
            session_id = session_id,
            persist    = settings.session.persist,
        )
        self.session_id       = self._storage.session_id
        self.loop:            AgentLoop

    @classmethod
    async def open(
        cls,
        conn:        Any,
        settings:    NerdvanaSettings,
        mcp_servers: list[Any] | None = None,
        session_id:  str | None       = None,
        restore:     bool             = False,
    ) -> AcpSession:
        """A session in ``settings.cwd``, with the project's and the editor's MCP servers connected.

        *restore* loads the transcript of *session_id* into the conversation.
        """
        session = cls(conn, settings, session_id)
        await session._connect_mcp(session_mcp_configs(settings.cwd, mcp_servers))
        session._build_loop()
        if restore:
            session.loop.restore_history()
        session._pump_task = asyncio.create_task(session._pump())
        return session

    async def _connect_mcp(self, configs: dict[str, McpServerConfig]) -> None:
        if not configs:
            return
        manager = McpManager(configs)
        for name, status in (await manager.connect_all()).items():
            logger.log(logging.INFO if status.startswith("connected") else logging.WARNING, "MCP %s: %s", name, status)
        self._mcp = manager

    def _build_loop(self) -> None:
        """Create the registry and the loop with the session's directory as the working directory.

        The language-server tools take the project root from the process directory when they are created, and
        one process serves sessions of different directories, so the change lasts for this synchronous step only.
        """
        with contextlib.chdir(self.cwd):
            self.loop = build_agent_loop(self.settings, ExecutionProfile(
                session           = self._storage,
                task_registry     = TaskRegistry(),
                mcp_tools         = self._mcp.get_all_tools() if self._mcp else [],
                on_thinking_chunk = self._on_thinking if self.settings.model.show_thinking else None,
                on_confirm        = self._confirm,
            ))
        self.loop.usage_listener = self._on_usage
        self.loop.hooks.register(HookEvent.BEFORE_TOOL, self._before_tool)
        self.loop.hooks.register(HookEvent.AFTER_TOOL, self._after_tool)

    # ------------------------------------------------------------------ outbound

    def _enqueue(self, update: Any) -> None:
        self._outbox.put_nowait(update)

    async def _pump(self) -> None:
        """Send queued updates one at a time; a failed send is logged and the next one still goes out."""
        while True:
            update = await self._outbox.get()
            try:
                await self._conn.session_update(session_id=self.session_id, update=update)
            except Exception:  # noqa: BLE001
                logger.warning("session update could not be sent", exc_info=True)
            finally:
                self._outbox.task_done()

    async def flush(self) -> None:
        """Wait until every queued update has been sent."""
        await self._outbox.join()

    def publish_commands(self) -> None:
        """Tell the editor which slash commands the project defines."""
        self._enqueue(update_available_commands(available_commands(self._commands)))

    async def replay(self) -> None:
        """Send the conversation so far as user and agent message chunks (the answer to ``session/load``)."""
        for message in self.loop.state.messages:
            text = _message_text(message)
            if not text:
                continue
            if message.role == Role.USER:
                self._enqueue(update_user_message_text(text))
            elif message.role == Role.ASSISTANT:
                self._enqueue(update_agent_message_text(text))
        await self.flush()

    # ------------------------------------------------------------------ tool calls

    def _announce(self, tool_uses: list[dict[str, Any]]) -> None:
        """Tell the editor about the calls of one assistant message, before any of them runs."""
        for use in tool_uses:
            call = _Call(str(use["id"]), str(use["name"]), use.get("input") or {})
            self._calls[call.tool_use_id] = call
            diff = tool_diff(call.name, call.tool_input, self.cwd)
            self._enqueue(start_tool_call(
                call.tool_use_id,
                tool_title(call.name, call.tool_input, self.cwd),
                kind      = tool_kind(call.name),
                status    = "pending",
                content   = diff or None,
                locations = tool_locations(call.name, call.tool_input, self.cwd) or None,
                raw_input = call.tool_input,
            ))
            if call.name == "TodoWrite":
                self._enqueue(update_plan(plan_entries(call.tool_input)))

    def _find_call(self, name: str, tool_input: dict[str, Any]) -> _Call | None:
        """The announced call a hook is about: the same input object, else the first equal one."""
        calls = [c for c in self._calls.values() if c.name == name]
        return next((c for c in calls if c.tool_input is tool_input), None) or next(
            (c for c in calls if not c.started and c.tool_input == tool_input), None,
        )

    def _before_tool(self, context: HookContext) -> None:
        call = self._find_call(context.tool_name, context.tool_input)
        if call is not None:
            call.started = True
            self._enqueue(update_tool_call(call.tool_use_id, status="in_progress"))

    def _after_tool(self, context: HookContext) -> None:
        result = context.tool_result
        call   = self._calls.pop(getattr(result, "tool_use_id", ""), None)
        if call is None:
            return
        self._enqueue(update_tool_call(
            call.tool_use_id,
            status     = "failed" if result.is_error else "completed",
            content    = result_content(result.content) or None,
            raw_output = result.content,
        ))

    # ------------------------------------------------------------------ permission

    async def _confirm(self, tool_name: str, message: str) -> bool:
        """The loop's confirm callback: ask the editor, remembering an always-allow answer for the rest of the session."""
        async with self._confirm_lock:
            call = next((c for c in self._calls.values() if c.name == tool_name and not c.started and not c.asked), None)
            if call is not None:
                call.asked = True
            tool_input = call.tool_input if call else {}
            key        = approval_key(tool_name, tool_input)
            if key in self._decisions:
                return self._decisions[key]
            await self.flush()
            option = await self._request_permission(call, tool_name, message)
            granted = option in ("allow_once", "allow_always")
            if option == "allow_always":
                self._decisions[key] = True
            return granted

    async def _request_permission(self, call: _Call | None, tool_name: str, message: str) -> str:
        """The id of the option the user chose; ``cancelled`` when the editor cancelled or could not be asked."""
        tool_input = call.tool_input if call else {}
        if call is None:
            self._permission_seq += 1
        tool_call = ToolCallUpdate(
            tool_call_id = call.tool_use_id if call else f"permission-{self._permission_seq}",
            title        = tool_title(tool_name, tool_input, self.cwd),
            kind         = tool_kind(tool_name),
            status       = "pending",
            content      = text_content(message) or None,
            locations    = tool_locations(tool_name, tool_input, self.cwd) or None,
            raw_input    = tool_input,
        )
        try:
            response = await self._conn.request_permission(
                session_id=self.session_id, tool_call=tool_call, options=_PERMISSION_OPTIONS,
            )
        except (ConnectionError, RequestError):
            logger.warning("permission request for %s failed; denying", tool_name, exc_info=True)
            return "cancelled"
        outcome = response.outcome
        return outcome.option_id if outcome.outcome == "selected" else "cancelled"

    # ------------------------------------------------------------------ usage and thinking

    def _on_usage(self, usage: dict[str, Any]) -> None:
        self._enqueue(UsageUpdate(
            session_update = "usage_update",
            used           = int(usage.get("input_tokens", 0)),
            size           = self.settings.session.max_context_tokens,
            cost           = Cost(amount=round(self.loop.total_cost_usd(), 6), currency="USD"),
        ))

    def _on_thinking(self, buffer: str) -> None:
        """The loop reports the whole thought so far; the editor gets what is new."""
        delta = buffer[len(self._thought_seen):] if buffer.startswith(self._thought_seen) else buffer
        self._thought_seen = buffer
        if delta:
            self._enqueue(update_agent_thought_text(delta))

    # ------------------------------------------------------------------ the prompt turn

    async def prompt(self, blocks: list[Any]) -> PromptResponse:
        """Run one prompt turn and answer with how it ended."""
        if self._turn is not None and not self._turn.done():
            raise RequestError.invalid_request({"message": "a prompt is already running in this session"})
        text, images = prompt_parts(blocks)
        before       = self.loop.usage_summary()
        self._cancelled    = False
        self._thought_seen = ""
        self._turn         = asyncio.ensure_future(self._run_turn(expand_command(text, self._commands), images))
        try:
            stop = await self._turn
        except asyncio.CancelledError:
            if not self._cancelled:
                raise
            stop = "cancelled"
        except RequestError:
            await self.flush()
            raise
        except Exception as exc:
            logger.exception("prompt turn failed")
            await self.flush()
            raise RequestError.internal_error({"details": f"{type(exc).__name__}: {exc}"}) from exc
        finally:
            self._calls.clear()
        await self.flush()
        return PromptResponse(stop_reason=stop, usage=self._turn_usage(before))

    async def _run_turn(self, text: str, images: list[dict[str, Any]]) -> StopReason:
        held: list[str] = []
        async with contextlib.aclosing(self.loop.run(text, images or None)) as stream:
            async for chunk in stream:
                self._relay(chunk, held)
        return self._stop_reason(held)

    def _relay(self, chunk: str, held: list[str]) -> None:
        """Turn one string of the loop's output into an update; progress markers are covered by the tool hooks."""
        event = classify_chunk(chunk)
        if event["type"] == "text":
            if chunk:
                self._enqueue(update_agent_message_text(chunk))
        elif event["type"] == "notice":
            action = _notice_class(chunk)
            if action == "hold":
                held.append(event["text"])
            elif action == "show":
                self._enqueue(update_agent_message_text(event["text"] + "\n"))

    def _stop_reason(self, held: list[str]) -> StopReason:
        """The ACP stop reason of the finished run; a failed run raises the error the loop reported."""
        stop = self.loop.last_stop
        if stop in _ERROR_STOPS:
            raise RequestError.internal_error({"details": " ".join(held) or stop, "stop": stop})
        for note in held:
            self._enqueue(update_agent_message_text(note + "\n"))
        return _STOP_REASONS.get(stop, "end_turn")

    def _turn_usage(self, before: dict[str, int]) -> Usage:
        after = self.loop.usage_summary()
        delta = {key: after[key] - before.get(key, 0) for key in after}
        return Usage(
            total_tokens        = delta["input_tokens"] + delta["output_tokens"],
            input_tokens        = delta["input_tokens"],
            output_tokens       = delta["output_tokens"],
            cached_read_tokens  = delta["cache_read_tokens"],
            cached_write_tokens = delta["cache_write_tokens"],
        )

    def cancel(self) -> None:
        """Abort the running turn: the loop is cancelled and ``session/prompt`` answers ``cancelled``."""
        if self._turn is not None and not self._turn.done():
            self._cancelled = True
            self._turn.cancel()

    async def close(self) -> None:
        """End the session: stop the turn, fire the session-end hooks and disconnect the MCP servers."""
        self.cancel()
        if self._turn is not None:
            await asyncio.gather(self._turn, return_exceptions=True)
        self.loop.close_session("exit")
        if self._mcp is not None:
            await self._mcp.disconnect_all()
        if self._pump_task is not None:
            self._pump_task.cancel()
            await asyncio.gather(self._pump_task, return_exceptions=True)
