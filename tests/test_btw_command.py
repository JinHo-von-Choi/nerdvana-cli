"""/btw: a side question that leaves the conversation as it was.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

from typing import Any

from nerdvana_cli.providers.base import ProviderEvent
from nerdvana_cli.ui.slash.btw_command import handle_btw


class _Provider:
    def __init__(self, events: list[ProviderEvent]) -> None:
        self.events = events
        self.calls: list[tuple[str, list[dict[str, Any]], list[Any]]] = []

    async def stream(self, system_prompt: str, messages: Any, tools: Any) -> Any:
        self.calls.append((system_prompt, [dict(m) for m in messages], list(tools)))
        for event in self.events:
            yield event


class _Loop:
    def __init__(self, provider: _Provider) -> None:
        self.provider = provider
        self.history  = [{"role": "user", "content": "earlier"}, {"role": "assistant", "content": "reply"}]
        self.usage: list[dict[str, int]] = []

    def _to_provider_messages(self) -> list[dict[str, Any]]:
        return list(self.history)

    def build_system_prompt(self) -> str:
        return "SYSTEM"

    def _apply_usage(self, usage: dict[str, int], sent: Any) -> None:
        self.usage.append(usage)


class _App:
    def __init__(self, loop: _Loop | None, generating: bool = False) -> None:
        self._agent_loop    = loop
        self._is_generating = generating
        self.messages: list[str] = []

    def _add_chat_message(self, markup: str, **_: Any) -> None:
        self.messages.append(markup)


ANSWER = [
    ProviderEvent(type="content_delta", content="Because "),
    ProviderEvent(type="content_delta", content="of the cache."),
    ProviderEvent(type="usage", usage={"input_tokens": 100, "output_tokens": 5, "cache_read_tokens": 90}),
    ProviderEvent(type="done", stop_reason="end_turn"),
]


async def test_the_answer_is_shown_and_the_history_is_left_alone() -> None:
    provider = _Provider(ANSWER)
    loop     = _Loop(provider)
    app      = _App(loop)
    await handle_btw(app, "why is it fast?")  # type: ignore[arg-type]
    assert "Because of the cache." in app.messages[0] and "btw: why is it fast?" in app.messages[0]
    assert len(loop.history) == 2


async def test_the_request_starts_like_the_agents_own_and_offers_no_tools() -> None:
    provider = _Provider(ANSWER)
    loop     = _Loop(provider)
    await handle_btw(_App(loop), "q")  # type: ignore[arg-type]
    system, messages, tools = provider.calls[0]
    assert system == "SYSTEM" and tools == []
    assert messages[:2] == loop.history and messages[2] == {"role": "user", "content": "q"}


async def test_what_the_question_cost_is_counted() -> None:
    loop = _Loop(_Provider(ANSWER))
    await handle_btw(_App(loop), "q")  # type: ignore[arg-type]
    assert loop.usage == [{"input_tokens": 100, "output_tokens": 5, "cache_read_tokens": 90}]


async def test_it_needs_a_question_an_idle_agent_and_reports_failures() -> None:
    app = _App(_Loop(_Provider(ANSWER)))
    await handle_btw(app, "  ")  # type: ignore[arg-type]
    assert "Usage" in app.messages[-1]
    busy = _App(_Loop(_Provider(ANSWER)), generating=True)
    await handle_btw(busy, "q")  # type: ignore[arg-type]
    assert "idle" in busy.messages[-1]
    failing = _App(_Loop(_Provider([ProviderEvent(type="error", error="boom")])))
    await handle_btw(failing, "q")  # type: ignore[arg-type]
    assert "failed: boom" in failing.messages[-1]
    none = _App(None)
    await handle_btw(none, "q")  # type: ignore[arg-type]
    assert "idle" in none.messages[-1]


def test_the_command_is_registered_and_listed() -> None:
    from nerdvana_cli.ui.command_dispatcher import _build_handler_map
    from nerdvana_cli.ui.widgets.command_menu import SLASH_COMMANDS

    assert "/btw" in _build_handler_map() and "/btw" in {name for name, _ in SLASH_COMMANDS}
