"""Hook events for compaction, model switches and instructions, and the command hooks that bind them.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from nerdvana_cli.core import model_failover
from nerdvana_cli.core.agent_loop import AgentLoop
from nerdvana_cli.core.config.settings import NerdvanaSettings
from nerdvana_cli.core.context.loop_context import session_start_context
from nerdvana_cli.core.hooks.command_hooks import CommandHook, make_handler, parse_hooks
from nerdvana_cli.core.hooks.hooks import HookContext, HookEngine, HookEvent, HookResult
from nerdvana_cli.core.state import signals
from nerdvana_cli.core.state.session import SessionStorage
from nerdvana_cli.core.tool import ToolRegistry
from nerdvana_cli.providers.base import ProviderEvent
from nerdvana_cli.providers.errors import RETRYABLE
from nerdvana_cli.types import Message, Role

NEW_EVENTS = (
    HookEvent.PERMISSION_DENIED, HookEvent.PRE_COMPACT, HookEvent.POST_COMPACT,
    HookEvent.PRE_MODEL_SWITCH, HookEvent.POST_MODEL_SWITCH, HookEvent.INSTRUCTIONS_LOADED,
)


# ---------------------------------------------------------------------------
# The engine
# ---------------------------------------------------------------------------


def test_the_engine_has_twelve_events_with_the_documented_names() -> None:
    assert len(HookEvent) == 12
    assert [event.value for event in NEW_EVENTS] == [
        "permission_denied", "pre_compact", "post_compact", "pre_model_switch", "post_model_switch", "instructions_loaded",
    ]


def test_emit_hands_the_payload_to_handlers_and_returns_their_answers() -> None:
    engine = HookEngine()
    seen: list[HookContext] = []

    def handler(context: HookContext) -> HookResult:
        seen.append(context)
        return HookResult(message="hint")

    engine.register(HookEvent.PRE_COMPACT, handler)
    answers = engine.emit(HookEvent.PRE_COMPACT, "settings", tokens=5, messages=2)
    assert [a.message for a in answers] == ["hint"]
    assert (seen[0].event, seen[0].settings, seen[0].extra) == (HookEvent.PRE_COMPACT, "settings", {"tokens": 5, "messages": 2})
    assert engine.emit(HookEvent.POST_COMPACT, None) == []


# ---------------------------------------------------------------------------
# Command hooks bind the new events
# ---------------------------------------------------------------------------


def _hook(event: HookEvent, command: str, match: str = "*") -> CommandHook:
    return CommandHook(event=event, command=command, match=match, timeout=5.0)


def test_every_new_event_can_be_named_in_hooks_yml() -> None:
    text = "hooks:\n" + "".join(f"  - event: {event.value}\n    command: ./x.sh\n" for event in NEW_EVENTS)
    assert [hook.event for hook in parse_hooks(text)] == list(NEW_EVENTS)


def test_the_command_receives_the_details_of_the_event(tmp_path: Path) -> None:
    out     = tmp_path / "stdin.json"
    handler = make_handler(_hook(HookEvent.PRE_MODEL_SWITCH, f"cat > {out}"), str(tmp_path))
    handler(HookContext(event=HookEvent.PRE_MODEL_SWITCH, extra={"from_model": "a", "to_model": "b", "reason": "fallback"}))
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["event"] == "pre_model_switch" and payload["details"] == {"from_model": "a", "to_model": "b", "reason": "fallback"}
    assert "tool_name" not in payload


def test_a_denial_hook_gets_the_call_and_is_filtered_by_tool_name(tmp_path: Path) -> None:
    out     = tmp_path / "stdin.json"
    handler = make_handler(_hook(HookEvent.PERMISSION_DENIED, f"cat > {out}; echo try a narrower command; exit 2", match="Bash"), str(tmp_path))
    context = HookContext(event=HookEvent.PERMISSION_DENIED, tool_name="Bash", tool_input={"command": "rm -rf /"}, extra={"source": "policy", "reason": "no"})
    result  = handler(context)
    assert (result.allow, result.message) == (True, "try a narrower command")
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["tool_name"] == "Bash" and payload["tool_input"] == {"command": "rm -rf /"} and payload["details"]["source"] == "policy"
    context.tool_name = "FileWrite"
    assert handler(context) == HookResult()


@pytest.mark.parametrize(("event", "allow", "message"), [
    (HookEvent.PRE_COMPACT, False, "keep the history"),
    (HookEvent.PERMISSION_DENIED, True, "keep the history"),
    (HookEvent.POST_COMPACT, True, ""),
    (HookEvent.PRE_MODEL_SWITCH, True, ""),
    (HookEvent.POST_MODEL_SWITCH, True, ""),
    (HookEvent.INSTRUCTIONS_LOADED, True, ""),
])
def test_exit_code_two_means_something_only_where_the_documentation_says(tmp_path: Path, event: HookEvent, allow: bool, message: str) -> None:
    result = make_handler(_hook(event, "echo keep the history; exit 2"), str(tmp_path))(HookContext(event=event))
    assert (result.allow, result.message) == (allow, message)


def test_exit_zero_and_other_codes_change_nothing(tmp_path: Path) -> None:
    for command in ("exit 0", "exit 1", "exit 3"):
        assert make_handler(_hook(HookEvent.PRE_COMPACT, command), str(tmp_path))(HookContext(event=HookEvent.PRE_COMPACT)) == HookResult()


# ---------------------------------------------------------------------------
# A loop to fire them in
# ---------------------------------------------------------------------------


class _Scripted:
    """Fails with the given events first, then answers."""

    def __init__(self, failures: list[ProviderEvent] | None = None) -> None:
        self.failures = list(failures or [])

    async def stream(self, system_prompt: str, messages: Any, tools: Any) -> AsyncIterator[ProviderEvent]:
        if self.failures:
            yield self.failures.pop(0)
            return
        yield ProviderEvent(type="content_delta", content="ok")
        yield ProviderEvent(type="done", stop_reason="end_turn")


def _loop(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, provider: Any = None, **session: Any) -> tuple[AgentLoop, list[HookContext]]:
    monkeypatch.setenv("NERDVANA_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setattr(AgentLoop, "create_provider_from_settings", lambda self: provider or _Scripted())
    monkeypatch.setattr(AgentLoop, "build_system_prompt", lambda self: "system")

    async def no_sleep(_delay: float) -> None:
        return None

    monkeypatch.setattr(model_failover.asyncio, "sleep", no_sleep)
    settings                = NerdvanaSettings()
    settings.cwd            = str(tmp_path)
    settings.model.provider = "anthropic"
    settings.model.model    = "primary"
    for key, value in session.items():
        setattr(settings.session, key, value)
    loop = AgentLoop(settings=settings, registry=ToolRegistry(), session=SessionStorage(session_id="ev", storage_dir=str(tmp_path / "sessions")))
    seen: list[HookContext] = []
    for event in NEW_EVENTS:
        loop.hooks.register(event, lambda context: seen.append(context))  # type: ignore[arg-type,return-value]
    return loop, seen


def _history() -> list[Message]:
    filler = "x" * 400
    return [Message(role=Role.USER if n % 2 == 0 else Role.ASSISTANT, content=filler) for n in range(8)]


# ---------------------------------------------------------------------------
# Compaction
# ---------------------------------------------------------------------------


async def test_compaction_fires_before_and_after_with_what_changed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    loop, seen = _loop(monkeypatch, tmp_path)
    loop.state.messages = _history()
    loop._compaction_state.consecutive_failures = loop._compaction_state.max_failures      # the naive path
    [status async for status in loop._maybe_compact_messages(800, 200)]
    assert [context.event for context in seen] == [HookEvent.PRE_COMPACT, HookEvent.POST_COMPACT]
    assert seen[0].extra == {"tokens": 800, "messages": 8}
    post = seen[1].extra
    assert (post["tokens_before"], post["messages_before"], post["strategy"]) == (800, 8, "naive")
    assert post["messages_after"] == len(loop.state.messages) < 8


async def test_a_model_summary_is_reported_as_the_ai_strategy(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    loop, seen = _loop(monkeypatch, tmp_path)
    loop.state.messages = _history()

    async def summarise(messages: Any, provider: Any, state: Any, *, prompt: str) -> Message:
        state.record_success()
        return Message(role=Role.USER, content="[summary]")

    monkeypatch.setattr("nerdvana_cli.core.agent_loop.ai_compact", summarise)
    statuses = [status async for status in loop._maybe_compact_messages(800, 200)]
    assert statuses[-1].endswith("done")
    assert seen[-1].event == HookEvent.POST_COMPACT and seen[-1].extra["strategy"] == "ai"


async def test_a_pre_compact_hook_can_refuse_and_the_history_stays(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    loop, seen = _loop(monkeypatch, tmp_path)
    loop.hooks.register(HookEvent.PRE_COMPACT, lambda context: HookResult(allow=False, message="audit in progress"))
    loop.state.messages = _history()
    before              = list(loop.state.messages)
    statuses            = [status async for status in loop._maybe_compact_messages(800, 200)]
    assert statuses == [] and loop.state.messages == before
    assert [context.event for context in seen] == [HookEvent.PRE_COMPACT]          # no POST_COMPACT for a compaction that did not run
    assert loop._signals[signals.COMPACTION] == 0
    skipped = [entry for entry in loop.session.replay() if entry.get("subtype") == "compaction_skipped"]
    assert skipped and skipped[0]["reason"] == "audit in progress"


async def test_a_refusal_without_a_message_still_has_a_reason(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    loop, _ = _loop(monkeypatch, tmp_path)
    loop.hooks.register(HookEvent.PRE_COMPACT, lambda context: HookResult(allow=False))
    loop.state.messages = _history()
    [status async for status in loop._maybe_compact_messages(800, 200)]
    assert any(entry.get("reason") == "a PRE_COMPACT hook refused" for entry in loop.session.replay())


# ---------------------------------------------------------------------------
# Model switches
# ---------------------------------------------------------------------------


def _switches(seen: list[HookContext]) -> list[tuple[HookEvent, str, str, str]]:
    return [
        (context.event, f"{context.extra['from_provider']}:{context.extra['from_model']}", f"{context.extra['to_provider']}:{context.extra['to_model']}", context.extra["reason"])
        for context in seen if context.event in (HookEvent.PRE_MODEL_SWITCH, HookEvent.POST_MODEL_SWITCH)
    ]


async def test_a_fallback_and_the_way_back_are_announced(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    provider = _Scripted([ProviderEvent(type="error", error="busy", error_kind=RETRYABLE)])
    loop, seen = _loop(monkeypatch, tmp_path, provider)
    loop.settings.model.max_retries     = 0
    loop.settings.model.fallback_models = ["secondary"]
    [chunk async for chunk in loop.run("hi")]
    assert _switches(seen) == [
        (HookEvent.PRE_MODEL_SWITCH,  "anthropic:primary",   "anthropic:secondary", "fallback"),
        (HookEvent.POST_MODEL_SWITCH, "anthropic:primary",   "anthropic:secondary", "fallback"),
        (HookEvent.PRE_MODEL_SWITCH,  "anthropic:secondary", "anthropic:primary",   "restore"),
        (HookEvent.POST_MODEL_SWITCH, "anthropic:secondary", "anthropic:primary",   "restore"),
    ]


async def test_an_escalation_is_announced_once_and_a_plain_prompt_announces_nothing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    quiet, seen = _loop(monkeypatch, tmp_path)
    [chunk async for chunk in quiet.run("hi")]
    assert _switches(seen) == []                                    # same model before and after: no event

    loop, seen = _loop(monkeypatch, tmp_path, escalation_model="stronger")
    loop._signals[signals.VERIFY_FAILED] += 1
    [chunk async for chunk in loop.run("hi")]
    assert _switches(seen) == [
        (HookEvent.PRE_MODEL_SWITCH,  "anthropic:primary", "anthropic:stronger", "escalation"),
        (HookEvent.POST_MODEL_SWITCH, "anthropic:primary", "anthropic:stronger", "escalation"),
    ]


# ---------------------------------------------------------------------------
# Instructions
# ---------------------------------------------------------------------------


async def test_the_instruction_documents_are_announced_when_a_session_starts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NERDVANA_DATA_HOME", str(tmp_path / "data"))
    (tmp_path / "NIRNA.md").write_text("project rules", encoding="utf-8")
    (tmp_path / "AGENTS.md").write_text("agent rules here", encoding="utf-8")
    engine = HookEngine()
    seen: list[HookContext] = []
    engine.register(HookEvent.INSTRUCTIONS_LOADED, lambda context: seen.append(context))  # type: ignore[arg-type,return-value]
    settings     = NerdvanaSettings()
    settings.cwd = str(tmp_path)
    await session_start_context(settings, engine, [])
    (context,) = seen
    files = {Path(item["path"]).name: item for item in context.extra["files"]}
    assert files["NIRNA.md"] == {"path": str(tmp_path / "NIRNA.md"), "type": "project", "chars": len("project rules")}
    assert files["AGENTS.md"]["chars"] == len("agent rules here")
