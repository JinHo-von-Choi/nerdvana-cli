"""The advisor: what it is sent, how often it may be asked, what it costs and when it refuses.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

import sqlite3
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from nerdvana_cli.core.advisor import MAX_REPLY_TOKENS, Advisor, Reply, provider_completion, render_context
from nerdvana_cli.core.agent_loop import AgentLoop
from nerdvana_cli.core.config.settings import NerdvanaSettings
from nerdvana_cli.core.state import signals
from nerdvana_cli.core.state.session import SessionStorage
from nerdvana_cli.core.telemetry.analytics import AnalyticsWriter, CallOrigin, PricingTable
from nerdvana_cli.core.tool import ToolRegistry
from nerdvana_cli.providers.base import PROVIDER_KEY_ENVVARS, ProviderEvent, ProviderName
from nerdvana_cli.tools.advisor_tool import AdvisorTool
from nerdvana_cli.types import Message, Role

PRICING = (
    "acme:\n"
    "  priced: {input_per_1m: 1000000.0, output_per_1m: 1000000.0}\n"
    "  big:    {input_per_1m: 2000000.0, output_per_1m: 2000000.0}\n"
)
USAGE = {"input_tokens": 1000, "output_tokens": 100}


class _Complete:
    """A one-shot completion function that records what it was asked and answers with ``reply``."""

    def __init__(self, reply: Reply | Exception | None = None) -> None:
        self.reply = reply if reply is not None else Reply("Prefer the smaller change.", dict(USAGE))
        self.calls: list[tuple[str | None, str, str, str]] = []

    async def __call__(self, provider: str | None, model: str, system: str, prompt: str) -> Reply:
        self.calls.append((provider, model, system, prompt))
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


def make_loop(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, complete: _Complete | None = None, origin: CallOrigin | None = None, **advisor: Any,
) -> tuple[AgentLoop, _Complete, Path]:
    """A loop with the advisor on (model ``big`` on the session's own provider) and a fake completion function."""
    monkeypatch.setenv("NERDVANA_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setattr(AgentLoop, "create_provider_from_settings", lambda self: None)
    monkeypatch.setattr(AgentLoop, "build_system_prompt", lambda self: "system")
    pricing = tmp_path / "pricing.yml"
    pricing.write_text(PRICING, encoding="utf-8")
    table = PricingTable(pricing_path=pricing)
    db    = tmp_path / "a.sqlite"
    settings                = NerdvanaSettings()
    settings.cwd            = str(tmp_path)
    settings.model.provider = "acme"
    settings.model.model    = "priced"
    settings.advisor.enabled = True
    settings.advisor.model   = "big"
    for key, value in advisor.items():
        setattr(settings.advisor, key, value)
    loop = AgentLoop(
        settings         = settings,
        registry         = ToolRegistry(),
        session          = SessionStorage(session_id="adv", storage_dir=str(tmp_path / "s")),
        analytics_writer = AnalyticsWriter(db_path=db, pricing_table=table, enabled=True),
        pricing_table    = table,
        origin           = origin,
    )
    fake         = complete or _Complete()
    loop.advisor = Advisor(loop, fake)
    return loop, fake, db


def _history(count: int) -> list[Message]:
    return [Message(role=Role.USER if n % 2 == 0 else Role.ASSISTANT, content=f"message-{n:02d}") for n in range(count)]


# ---------------------------------------------------------------------------
# The answer
# ---------------------------------------------------------------------------


async def test_the_answer_carries_the_advice_and_the_consultations_left(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    loop, fake, _ = make_loop(monkeypatch, tmp_path)
    advice = await loop.advisor.advise("Which approach?")
    assert advice.ok
    assert advice.text.startswith("Prefer the smaller change.")
    assert advice.text.endswith("[Advisor consultations left in this run: 2]")
    provider, model, system, prompt = fake.calls[0]
    assert (provider, model) == (None, "big")
    assert "no tools" in system
    assert "Question from the agent:\nWhich approach?" in prompt


async def test_a_provider_prefix_names_the_provider_of_the_request(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv(PROVIDER_KEY_ENVVARS[ProviderName.OPENAI][0], "k-openai")
    loop, fake, _ = make_loop(monkeypatch, tmp_path, model="openai:gpt-5")
    await loop.advisor.advise("q")
    assert fake.calls[0][:2] == ("openai", "gpt-5")


# ---------------------------------------------------------------------------
# What is sent
# ---------------------------------------------------------------------------


async def test_only_the_recent_messages_are_sent_never_the_whole_history(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    loop, fake, _ = make_loop(monkeypatch, tmp_path)
    loop.state.messages = _history(30)
    await loop.advisor.advise("q")
    prompt = fake.calls[0][3]
    assert "message-29" in prompt and "message-18" in prompt
    assert "message-17" not in prompt and "message-00" not in prompt


async def test_the_window_follows_the_setting(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    loop, fake, _ = make_loop(monkeypatch, tmp_path, max_context_messages=3)
    loop.state.messages = _history(30)
    await loop.advisor.advise("q")
    prompt = fake.calls[0][3]
    assert [f"message-{n:02d}" in prompt for n in (26, 27, 28, 29)] == [False, True, True, True]


def test_tool_output_is_cut_hardest_and_keeps_its_start_and_end() -> None:
    call    = {"id": "c1", "name": "Bash", "input": {"command": "pytest " + "x" * 2000}}
    history = [
        Message(role=Role.ASSISTANT, content="[tool execution]", tool_uses=[call]),
        Message(role=Role.TOOL, content="START" + "o" * 10_000 + "END", tool_use_id="c1"),
        Message(role=Role.USER, content="u" * 10_000),
    ]
    text = render_context(history, 12)
    assert "[Bash result]" in text and "START" in text and "END" in text
    assert "characters cut" in text
    assert "[tool execution]" not in text
    assert "  call Bash " in text
    assert len(text) < 1200 + 3000 + 300 + 600           # the three bounds plus the markers


def test_an_error_result_and_an_image_block_are_named_in_the_transcript() -> None:
    history = [
        Message(role=Role.TOOL, content="boom", tool_use_id="gone", is_error=True),
        Message(role=Role.USER, content=[{"type": "text", "text": "look"}, {"type": "image", "source": {}}]),
    ]
    assert render_context(history, 12).splitlines() == ["[tool result (error)] boom", "[user] look [image]"]


async def test_secrets_are_masked_in_the_question_and_the_history_even_with_masking_off(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MY_SERVICE_TOKEN", "hunter2hunter2hunter2")
    loop, fake, _ = make_loop(monkeypatch, tmp_path)
    loop.settings.session.mask_secrets = False
    key   = "sk-ant-api03-" + "A" * 40
    gh    = "ghp_" + "b" * 36
    loop.state.messages = [
        Message(role=Role.USER, content=f"use {key} for the call"),
        Message(role=Role.TOOL, content=f"MY_SERVICE_TOKEN=hunter2hunter2hunter2 and {gh}", tool_use_id="x"),
    ]
    await loop.advisor.advise(f"is {key} fine to log?")
    prompt = fake.calls[0][3]
    for secret in (key, gh, "hunter2hunter2hunter2"):
        assert secret not in prompt
    assert "[REDACTED]" in prompt
    assert loop.signal_summary()[signals.SECRET_MASKED] >= 4


async def test_the_session_api_key_is_masked(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    loop, fake, _ = make_loop(monkeypatch, tmp_path)
    loop.settings.model.api_key = "plainlooking-key-1234"
    loop.state.messages = [Message(role=Role.USER, content="key is plainlooking-key-1234")]
    await loop.advisor.advise("q")
    assert "plainlooking-key-1234" not in fake.calls[0][3]


async def test_the_question_is_bounded(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    loop, fake, _ = make_loop(monkeypatch, tmp_path)
    await loop.advisor.advise("q" * 50_000)
    assert len(fake.calls[0][3]) < 3000


async def test_the_reason_a_signal_prompted_the_question_is_included(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    loop, fake, _ = make_loop(monkeypatch, tmp_path)
    await loop.advisor.advise("what now?", "verify_failed x2")
    assert "Why it came up: verify_failed x2" in fake.calls[0][3]


# ---------------------------------------------------------------------------
# The cap
# ---------------------------------------------------------------------------


async def test_the_call_cap_is_per_run_and_the_next_run_starts_again(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    loop, fake, _ = make_loop(monkeypatch, tmp_path, max_calls=2)
    assert [(await loop.advisor.advise("q")).ok for _ in range(3)] == [True, True, False]
    refused = await loop.advisor.advise("q")
    assert "2 consultation(s) per run" in refused.text and not refused.ok
    assert len(fake.calls) == 2
    loop.advisor.start_run()
    assert (await loop.advisor.advise("q")).ok
    assert len(fake.calls) == 3


async def test_zero_calls_refuses_everything(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    loop, fake, _ = make_loop(monkeypatch, tmp_path, max_calls=0)
    assert not (await loop.advisor.advise("q")).ok and fake.calls == []


# ---------------------------------------------------------------------------
# Cost
# ---------------------------------------------------------------------------


async def test_a_consultation_is_a_ledger_row_of_the_advisor_priced_for_its_model(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    loop, _, db = make_loop(monkeypatch, tmp_path)
    loop._last_tool = "Bash"
    loop.turns_used = 4
    await loop.advisor.advise("q")
    conn = sqlite3.connect(db)
    try:
        rows = conn.execute("SELECT provider, model, input_tokens, output_tokens, cost_usd, agent_id, agent_type, turn, last_tool FROM api_calls").fetchall()
    finally:
        conn.close()
    assert rows == [("acme", "big", 1000, 100, pytest.approx(2200.0), "advisor", "advisor", 4, "Bash")]


async def test_the_cost_and_tokens_count_for_the_session(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    loop, _, _ = make_loop(monkeypatch, tmp_path)
    await loop.advisor.advise("q")
    assert loop.session_cost_usd() == pytest.approx(2200.0) and loop.total_cost_usd() == pytest.approx(2200.0)
    assert loop.usage_summary()["input_tokens"] == 1000 and loop.usage_summary()["output_tokens"] == 100


async def test_the_cost_limit_sees_the_advisors_spend(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    loop, fake, _ = make_loop(monkeypatch, tmp_path)
    loop.settings.session.max_cost_usd = 1000.0
    assert loop.limits.exhausted() == ("", "")
    assert (await loop.advisor.advise("q")).ok
    assert loop.limits.exhausted()[0] == "max_cost"
    refused = await loop.advisor.advise("q")
    assert not refused.ok and "limit" in refused.text and len(fake.calls) == 1


async def test_the_token_limit_refuses_too(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    loop, fake, _ = make_loop(monkeypatch, tmp_path)
    loop.settings.session.max_total_tokens = 1000
    assert (await loop.advisor.advise("q")).ok
    assert not (await loop.advisor.advise("q")).ok and len(fake.calls) == 1


async def test_a_failed_request_still_records_what_it_reported(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    loop, _, _ = make_loop(monkeypatch, tmp_path, _Complete(Reply("", dict(USAGE), "overloaded")))
    advice = await loop.advisor.advise("q")
    assert not advice.ok and "overloaded" in advice.text
    assert loop.session_cost_usd() == pytest.approx(2200.0)


async def test_the_advised_signal_counts_the_requests_made(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    loop, _, _ = make_loop(monkeypatch, tmp_path)
    await loop.advisor.advise("q")
    await loop.advisor.advise("q")
    assert loop.signal_summary()[signals.ADVISED] == 2


# ---------------------------------------------------------------------------
# Refusals
# ---------------------------------------------------------------------------


async def test_it_is_not_available_when_off_or_without_a_model(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    loop, fake, _ = make_loop(monkeypatch, tmp_path, enabled=False)
    assert not loop.advisor.available
    assert "not available" in (await loop.advisor.advise("q")).text
    loop.settings.advisor.enabled, loop.settings.advisor.model = True, ""
    assert not loop.advisor.available and not (await loop.advisor.advise("q")).ok
    assert fake.calls == []


async def test_a_sub_agent_never_consults(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    loop, fake, _ = make_loop(monkeypatch, tmp_path, origin=CallOrigin(agent_id="agent_1", agent_type="Explore"))
    assert not loop.advisor.available and not (await loop.advisor.advise("q")).ok and fake.calls == []


async def test_a_provider_without_a_credential_is_refused_with_a_clear_message(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    for name in PROVIDER_KEY_ENVVARS[ProviderName.OPENAI]:
        monkeypatch.delenv(name, raising=False)
    loop, fake, _ = make_loop(monkeypatch, tmp_path, model="openai:gpt-5")
    advice = await loop.advisor.advise("q")
    assert not advice.ok and "no credential for provider openai" in advice.text
    assert fake.calls == [] and loop.advisor.calls == 0


async def test_the_sessions_own_provider_needs_no_extra_credential(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    for name in PROVIDER_KEY_ENVVARS[ProviderName.OPENAI]:
        monkeypatch.delenv(name, raising=False)
    loop, fake, _ = make_loop(monkeypatch, tmp_path, model="openai:gpt-5")
    loop.settings.model.provider = "openai"
    assert (await loop.advisor.advise("q")).ok and fake.calls[0][:2] == ("openai", "gpt-5")


@pytest.mark.parametrize("reply", [RuntimeError("socket closed"), Reply("   "), Reply("", {}, "429")])
async def test_a_request_that_fails_or_says_nothing_is_reported_not_raised(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, reply: Reply | Exception,
) -> None:
    loop, fake, _ = make_loop(monkeypatch, tmp_path, _Complete(reply))
    advice = await loop.advisor.advise("q")
    assert not advice.ok and advice.text
    assert len(fake.calls) == 1 and loop.advisor.calls == 1      # the attempt counted


# ---------------------------------------------------------------------------
# The real request function
# ---------------------------------------------------------------------------


class _Client:
    def __init__(self, result: dict[str, Any]) -> None:
        self.result = result
        self.sent: list[tuple[str, list[dict[str, Any]], list[Any]]] = []

    async def send(self, system: str, messages: list[dict[str, Any]], tools: list[Any]) -> dict[str, Any]:
        self.sent.append((system, messages, tools))
        return self.result


def _capture(monkeypatch: pytest.MonkeyPatch, client: _Client) -> list[dict[str, Any]]:
    built: list[dict[str, Any]] = []

    def _create(**kwargs: Any) -> _Client:
        built.append(kwargs)
        return client

    monkeypatch.setattr("nerdvana_cli.core.advisor.create_provider", _create)
    return built


async def test_the_real_request_is_one_plain_completion_without_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _Client({"content": "advice", "usage": {"input_tokens": 3, "output_tokens": 4}})
    built  = _capture(monkeypatch, client)
    settings = NerdvanaSettings()
    settings.model.provider, settings.model.api_key, settings.model.base_url = "acme", "own-key", "http://own"
    reply = await provider_completion(settings)(None, "big", "sys", "the prompt")
    assert reply == Reply("advice", {"input_tokens": 3, "output_tokens": 4})
    assert built[0]["provider"] == "acme" and built[0]["model"] == "big"
    assert (built[0]["api_key"], built[0]["base_url"]) == ("own-key", "http://own")
    assert built[0]["max_tokens"] == MAX_REPLY_TOKENS
    assert client.sent == [("sys", [{"role": "user", "content": "the prompt"}], [])]


async def test_another_provider_gets_its_key_from_the_environment_and_no_base_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(PROVIDER_KEY_ENVVARS[ProviderName.OPENAI][0], "env-key")
    built    = _capture(monkeypatch, _Client({"content": "x"}))
    settings = NerdvanaSettings()
    settings.model.provider, settings.model.api_key, settings.model.base_url = "acme", "own-key", "http://own"
    await provider_completion(settings)("openai", "gpt-5", "s", "p")
    assert (built[0]["provider"], built[0]["api_key"], built[0]["base_url"]) == ("openai", "env-key", "")


async def test_an_error_result_of_the_provider_becomes_the_replys_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _capture(monkeypatch, _Client({"content": "rate limited", "is_error": True}))
    settings = NerdvanaSettings()
    settings.model.provider = "acme"
    reply = await provider_completion(settings)(None, "big", "s", "p")
    assert reply.error == "rate limited" and reply.text == ""


# ---------------------------------------------------------------------------
# Through the loop: the Advisor tool
# ---------------------------------------------------------------------------


_DONE = [ProviderEvent(type="done", stop_reason="end_turn")]


class _Script:
    """Answers each request with the next list of events; ``payloads`` holds the messages of every request."""

    def __init__(self, responses: list[list[ProviderEvent]]) -> None:
        self.responses = list(responses)
        self.payloads: list[list[dict[str, Any]]] = []

    async def stream(self, system_prompt: str, messages: Any, tools: Any) -> AsyncIterator[ProviderEvent]:
        self.payloads.append([dict(m) for m in messages])
        for event in self.responses.pop(0) if self.responses else _DONE:
            yield event


def _advisor_call(call_id: str, question: str) -> list[ProviderEvent]:
    return [
        ProviderEvent(type="tool_use_complete", tool_use_id=call_id, tool_name="Advisor", tool_input_complete={"question": question}),
        ProviderEvent(type="done", stop_reason="tool_use"),
    ]


async def test_the_model_consults_the_advisor_through_the_tool_and_the_result_reaches_it(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    loop, fake, _ = make_loop(monkeypatch, tmp_path)
    script = _Script([_advisor_call("a1", "rewrite or patch?"), _DONE])
    monkeypatch.setattr(loop, "provider", script)
    loop.registry.register(AdvisorTool())
    async for _ in loop.run("fix the bug"):
        pass
    assert [c[3].count("rewrite or patch?") for c in fake.calls] == [1]
    result = [m for m in script.payloads[1] if m["role"] == "tool"][0]
    assert result["content"].startswith("Prefer the smaller change.") and not result["is_error"]
    assert loop.advisor.calls == 1
    assert loop.session_cost_usd() == pytest.approx(2200.0)


async def test_a_refusal_comes_back_to_the_model_as_an_error_result_and_the_run_goes_on(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    loop, fake, _ = make_loop(monkeypatch, tmp_path, max_calls=1)
    script = _Script([_advisor_call("a1", "one"), _advisor_call("a2", "two"), _DONE])
    monkeypatch.setattr(loop, "provider", script)
    loop.registry.register(AdvisorTool())
    async for _ in loop.run("go"):
        pass
    results = [m for m in script.payloads[2] if m["role"] == "tool"]
    assert [r["is_error"] for r in results] == [False, True]
    assert "per run are used up" in results[1]["content"]
    assert len(fake.calls) == 1 and loop.last_stop == "completed"


async def test_each_run_has_its_own_consultations(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    loop, fake, _ = make_loop(monkeypatch, tmp_path, max_calls=1)
    monkeypatch.setattr(loop, "provider", _Script([_advisor_call("a1", "one"), _DONE, _advisor_call("a2", "two"), _DONE]))
    loop.registry.register(AdvisorTool())
    for _ in range(2):
        async for _chunk in loop.run("go"):
            pass
    assert len(fake.calls) == 2


# ---------------------------------------------------------------------------
# Through the loop: advice before an escalation
# ---------------------------------------------------------------------------


class _Quiet:
    def __init__(self) -> None:
        self.payloads: list[list[dict[str, Any]]] = []

    async def stream(self, system_prompt: str, messages: Any, tools: Any) -> AsyncIterator[ProviderEvent]:
        self.payloads.append([dict(m) for m in messages])
        yield ProviderEvent(type="content_delta", content="ok")
        yield ProviderEvent(type="done", stop_reason="end_turn")


def _escalating(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, complete: _Complete | None = None, escalation: str = "claude-opus-5-5", **advisor: Any,
) -> tuple[AgentLoop, _Complete, list[str], _Quiet]:
    loop, fake, _ = make_loop(monkeypatch, tmp_path, complete, **{"on_signals": True, **advisor})
    loop.settings.session.escalation_model = escalation
    quiet    = _Quiet()
    switched: list[str] = []
    monkeypatch.setattr(loop, "provider", quiet)
    monkeypatch.setattr(loop.failover, "switch_model", lambda provider, model: switched.append(model))
    return loop, fake, switched, quiet


async def _run(loop: AgentLoop) -> str:
    return "".join([chunk async for chunk in loop.run("go")])


async def test_a_signal_at_its_threshold_asks_the_advisor_and_the_model_is_not_switched(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    loop, fake, switched, quiet = _escalating(monkeypatch, tmp_path)
    loop._signals[signals.VERIFY_FAILED] += 1
    output = await _run(loop)
    assert switched == []
    assert "Asked the advisor (big): verify_failed x1" in output
    assert len(fake.calls) == 1 and "Why it came up: verify_failed x1" in fake.calls[0][3]
    notes = [m["content"] for m in quiet.payloads[0] if m["role"] == "user" and str(m["content"]).startswith("[Advisor guidance")]
    assert len(notes) == 1 and "Prefer the smaller change." in notes[0] and "verify_failed x1" in notes[0]
    assert loop.signal_summary()[signals.ADVISED] == 1 and signals.ESCALATED not in loop.signal_summary()


async def test_the_advice_is_given_once_and_without_new_trouble_nothing_else_happens(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    loop, fake, switched, _ = _escalating(monkeypatch, tmp_path)
    loop._signals[signals.VERIFY_FAILED] += 1
    await _run(loop)
    await _run(loop)
    assert len(fake.calls) == 1 and switched == []


async def test_a_signal_that_keeps_coming_after_the_advice_switches_the_model(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    loop, fake, switched, _ = _escalating(monkeypatch, tmp_path)
    loop._signals[signals.VERIFY_FAILED] += 1
    await _run(loop)
    loop._signals[signals.VERIFY_FAILED] += 1
    output = await _run(loop)
    assert switched == ["claude-opus-5-5"] and "Escalating to" in output
    assert len(fake.calls) == 1                                  # the advisor is not asked a second time


async def test_another_signal_that_reaches_its_threshold_after_the_advice_also_switches(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    loop, _, switched, _ = _escalating(monkeypatch, tmp_path)
    loop._signals[signals.VERIFY_FAILED] += 1
    await _run(loop)
    loop._signals[signals.REPEAT_REFUSED] += 1
    await _run(loop)
    assert switched == ["claude-opus-5-5"]


@pytest.mark.parametrize("advisor", [{"max_calls": 0}, {"enabled": False}, {"model": ""}])
async def test_when_the_advisor_cannot_answer_the_model_is_switched_at_once(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, advisor: dict[str, Any]) -> None:
    loop, fake, switched, _ = _escalating(monkeypatch, tmp_path, **advisor)
    loop._signals[signals.VERIFY_FAILED] += 1
    output = await _run(loop)
    assert switched == ["claude-opus-5-5"] and fake.calls == [] and "Escalating to" in output


async def test_a_failed_request_to_the_advisor_falls_back_to_the_switch(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    loop, fake, switched, _ = _escalating(monkeypatch, tmp_path, _Complete(RuntimeError("down")))
    loop._signals[signals.VERIFY_FAILED] += 1
    await _run(loop)
    assert len(fake.calls) == 1 and switched == ["claude-opus-5-5"]


async def test_without_on_signals_the_escalation_is_what_it_was(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    loop, fake, switched, _ = _escalating(monkeypatch, tmp_path, on_signals=False)
    loop._signals[signals.VERIFY_FAILED] += 1
    await _run(loop)
    assert switched == ["claude-opus-5-5"] and fake.calls == []


async def test_without_an_escalation_model_the_advice_is_the_only_action(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    loop, fake, switched, _ = _escalating(monkeypatch, tmp_path, escalation="")
    loop._signals[signals.VERIFY_FAILED] += 1
    await _run(loop)
    loop._signals[signals.VERIFY_FAILED] += 3
    await _run(loop)
    assert len(fake.calls) == 1 and switched == []


async def test_below_the_thresholds_the_advisor_is_not_asked(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    loop, fake, switched, _ = _escalating(monkeypatch, tmp_path)
    loop.tool_executor.signals[signals.CAS_REJECTED] += 2
    await _run(loop)
    assert fake.calls == [] and switched == []


async def test_a_sub_agent_does_not_ask_on_a_signal(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    loop, fake, switched, _ = _escalating(monkeypatch, tmp_path)
    loop.origin = CallOrigin(agent_id="agent_1", agent_type="Explore")
    loop._signals[signals.VERIFY_FAILED] += 1
    await _run(loop)
    assert fake.calls == [] and switched == ["claude-opus-5-5"]
