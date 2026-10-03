"""The action classifier in the permission gate: shadow never changes a verdict, enforce changes only an allow.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

import sqlite3
from collections import Counter
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from nerdvana_cli.core.config.settings import NerdvanaSettings
from nerdvana_cli.core.execution.tool_executor import ToolExecutor
from nerdvana_cli.core.hooks.hooks import HookContext, HookEngine, HookEvent, HookResult
from nerdvana_cli.core.loop.agent_loop import AgentLoop
from nerdvana_cli.core.loop.run_limits import RunLimits
from nerdvana_cli.core.safety.classifier import ClassifierFeed, Completion
from nerdvana_cli.core.safety.policy import PermissionPolicy
from nerdvana_cli.core.state.session import SessionStorage
from nerdvana_cli.core.state.signals import (
    CLASSIFIER_ASK,
    CLASSIFIER_DENY,
    CLASSIFIER_ERROR,
    PERMISSION_POLICY,
    PERMISSION_USER,
)
from nerdvana_cli.core.telemetry.analytics import AnalyticsReader, AnalyticsWriter, PricingTable
from nerdvana_cli.core.tool import BaseTool, ToolCategory, ToolContext, ToolRegistry
from nerdvana_cli.providers.base import ProviderEvent
from nerdvana_cli.types import ToolResult

DENY_JSON = '{"verdict": "deny", "reason": "removes the build directory"}'
ASK_JSON  = '{"verdict": "ask", "reason": "reaches outside the project"}'
ALLOW_JSON = '{"verdict": "allow", "reason": "routine"}'


class _Tool(BaseTool[Any]):
    name             = "Probe"
    description_text = "probe"
    input_schema: dict[str, Any] = {"type": "object", "properties": {"command": {"type": "string"}}}

    def __init__(self, name: str = "Probe", category: ToolCategory = ToolCategory.WRITE) -> None:
        self.name     = name
        self.category = category  # type: ignore[misc]
        self.ran      = 0

    def parse_args(self, tool_input: dict[str, Any]) -> Any:
        return tool_input

    async def call(self, args: Any, context: Any, can_use_tool: Any = None, on_progress: Any = None) -> ToolResult:
        self.ran += 1
        return ToolResult(tool_use_id="", content="ran")


class _Fake:
    """A scripted completion function; each classification takes a pre-filter answer and then, on check, a verdict."""

    def __init__(self, *answers: str | Exception) -> None:
        self.answers = list(answers)
        self.calls   = 0

    async def __call__(self, system: str, user: str, max_tokens: int) -> Completion:
        self.calls += 1
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return Completion(answer, {"input_tokens": 10, "output_tokens": 1}, "acme", "small")


class _Rig:
    """An executor with a classifier on, a writer on a temporary database and a feed in the context."""

    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str, fake: Any, trust: str = "balanced",
                 tool: _Tool | None = None, allow: list[str] | None = None, hooks: HookEngine | None = None) -> None:
        self.db       = tmp_path / "a.sqlite"
        self.writer   = AnalyticsWriter(db_path=self.db)
        self.writer.start_session("s")
        self.fake     = fake
        self.tool     = tool or _Tool()
        self.answers: list[bool] = []
        self.asked:   list[str]  = []
        monkeypatch.setattr("nerdvana_cli.core.safety.classifier.provider_completion", lambda settings: fake)
        settings = NerdvanaSettings()
        settings.permissions.classifier = mode  # type: ignore[assignment]
        registry = ToolRegistry()
        registry.register(self.tool)
        self.executor = ToolExecutor(
            registry=registry, hooks=hooks or HookEngine(), settings=settings, analytics_writer=self.writer,
            policy=PermissionPolicy(trust_level=trust, always_allow=allow or []),
        )
        self.context = ToolContext(cwd=str(tmp_path), confirm=self._confirm)
        self.context.state["classifier_feed"] = ClassifierFeed(lambda: ["run the build"], lambda answer: 0.0, lambda: False)

    async def _confirm(self, name: str, message: str) -> bool:
        self.asked.append(message)
        return self.answers.pop(0)

    async def run(self, command: str = "rm -rf build", name: str | None = None) -> ToolResult:
        call = {"id": "1", "name": name or self.tool.name, "input": {"command": command}}
        return (await self.executor.run_batch([call], self.context))[0]

    def rows(self) -> list[tuple[str, str, str, str]]:
        with sqlite3.connect(self.db) as conn:
            return conn.execute("SELECT mode, verdict, outcome, reason FROM classifier_verdicts ORDER BY id").fetchall()


# ---------------------------------------------------------------------------
# Off, and what is never judged
# ---------------------------------------------------------------------------


async def test_with_the_classifier_off_nothing_is_asked_of_a_model(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _Fake()
    rig  = _Rig(tmp_path, monkeypatch, "off", fake)
    assert (await rig.run()).content == "ran" and fake.calls == 0 and rig.rows() == []


async def test_read_only_tools_and_always_allow_rules_are_never_judged(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _Fake()
    rig  = _Rig(tmp_path, monkeypatch, "enforce", fake, tool=_Tool("Peek", ToolCategory.READ))
    assert (await rig.run()).content == "ran"
    ruled = _Rig(tmp_path, monkeypatch, "enforce", fake, allow=["Probe(rm -rf build)"])
    assert (await ruled.run()).content == "ran"
    assert fake.calls == 0 and rig.rows() == []


async def test_a_policy_refusal_is_not_judged_and_stays_a_refusal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _Fake()
    rig  = _Rig(tmp_path, monkeypatch, "enforce", fake)
    rig.executor._permission._policy.always_deny = ["Probe"]
    result = await rig.run()
    assert result.is_error and result.content.startswith("Permission denied: ") and fake.calls == 0


# ---------------------------------------------------------------------------
# Shadow: record, never change
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("answers", [["check", DENY_JSON], ["check", ASK_JSON], ["ok"], [RuntimeError("down")]])
async def test_shadow_runs_the_call_whatever_the_classifier_says(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, answers: list[Any]) -> None:
    rig    = _Rig(tmp_path, monkeypatch, "shadow", _Fake(*answers))
    result = await rig.run()
    assert (result.content, result.is_error, rig.tool.ran, rig.asked) == ("ran", False, 1, [])
    ((mode, verdict, outcome, _),) = rig.rows()
    assert (mode, outcome) == ("shadow", "allow_auto")


async def test_shadow_judges_a_call_the_user_is_asked_about_and_keeps_both_answers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rig         = _Rig(tmp_path, monkeypatch, "shadow", _Fake("check", DENY_JSON, "ok"), trust="strict")
    rig.answers = [True, False]
    assert (await rig.run()).content == "ran"
    refused = await rig.run("ls")
    assert refused.content.startswith("Permission denied by user")
    assert [(verdict, outcome) for _, verdict, outcome, _ in rig.rows()] == [("deny", "allow_user"), ("allow", "deny_user")]
    assert len(rig.asked) == 2 and rig.tool.ran == 1


async def test_shadow_counts_what_it_would_have_done(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rig = _Rig(tmp_path, monkeypatch, "shadow", _Fake("check", DENY_JSON, "check", ASK_JSON, RuntimeError("x")))
    for command in ("a", "b", "c"):
        await rig.run(command)
    signals = rig.executor.signals
    assert (signals[CLASSIFIER_DENY], signals[CLASSIFIER_ASK], signals[CLASSIFIER_ERROR]) == (1, 1, 1)


# ---------------------------------------------------------------------------
# Enforce: only an allow changes, and only toward stricter
# ---------------------------------------------------------------------------


async def test_enforce_refuses_a_call_the_classifier_denies_and_says_why(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rig    = _Rig(tmp_path, monkeypatch, "enforce", _Fake("check", DENY_JSON))
    result = await rig.run()
    assert result.is_error and rig.tool.ran == 0 and rig.asked == []
    assert result.content == "Permission denied: the action classifier refused this call: removes the build directory"
    assert rig.executor.signals[CLASSIFIER_DENY] == 1 and rig.executor.signals[PERMISSION_POLICY] == 1
    assert rig.rows()[0][1:3] == ("deny", "deny_classifier")


async def test_enforce_turns_an_ask_verdict_into_a_question_the_user_answers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rig         = _Rig(tmp_path, monkeypatch, "enforce", _Fake("check", ASK_JSON, "check", ASK_JSON))
    rig.answers = [True, False]
    assert (await rig.run("a")).content == "ran"
    refused = await rig.run("b")
    assert refused.content == "Permission denied by user: Action classifier: reaches outside the project"
    assert rig.executor.signals[PERMISSION_USER] == 1 and rig.tool.ran == 1
    assert "reaches outside the project" in rig.asked[0]
    assert [row[2] for row in rig.rows()] == ["allow_user", "deny_user"]


async def test_enforce_lets_an_allowed_call_through_and_never_loosens_an_ask(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    allowed = _Rig(tmp_path, monkeypatch, "enforce", _Fake("ok"))
    assert (await allowed.run()).content == "ran" and allowed.rows()[0][2] == "allow_auto"
    # Strict trust makes the policy ask; the classifier is not consulted in enforce mode and cannot answer for the user.
    (tmp_path / "strict").mkdir()
    strict         = _Rig(tmp_path / "strict", monkeypatch, "enforce", _Fake(), trust="strict")
    strict.answers = [False]
    result = await strict.run()
    assert result.content.startswith("Permission denied by user") and strict.fake.calls == 0 and len(strict.asked) == 1


async def test_a_failed_classifier_in_enforce_mode_asks_and_without_a_terminal_that_refuses(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rig         = _Rig(tmp_path, monkeypatch, "enforce", _Fake(RuntimeError("down"), RuntimeError("down")))
    rig.answers = [True]
    assert (await rig.run("a")).content == "ran" and "classifier" in rig.asked[0].lower()
    rig.context.confirm = None                       # a run with nobody to ask
    refused = await rig.run("b")
    assert refused.is_error and refused.content.startswith("Permission denied by user")
    assert rig.executor.signals[CLASSIFIER_ERROR] == 2 and rig.tool.ran == 1


async def test_without_a_session_feed_enforce_cannot_judge_and_asks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rig         = _Rig(tmp_path, monkeypatch, "enforce", _Fake("ok"))
    rig.context.state.pop("classifier_feed")
    rig.answers = [False]
    assert (await rig.run()).content.startswith("Permission denied by user") and rig.fake.calls == 0


# ---------------------------------------------------------------------------
# The PermissionDenied hook
# ---------------------------------------------------------------------------


def _hooks(hint: str = "ask the user for a narrower command") -> tuple[HookEngine, list[HookContext]]:
    engine = HookEngine()
    seen: list[HookContext] = []

    def handler(context: HookContext) -> HookResult:
        seen.append(context)
        return HookResult(message=hint)

    engine.register(HookEvent.PERMISSION_DENIED, handler)
    return engine, seen


async def test_a_refusal_by_policy_the_user_or_the_classifier_fires_the_hook_and_takes_its_hint(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    engine, seen = _hooks()
    rig          = _Rig(tmp_path, monkeypatch, "enforce", _Fake("check", DENY_JSON, "check", ASK_JSON), hooks=engine)
    classifier   = await rig.run("a")
    rig.answers  = [False]
    by_user      = await rig.run("b")
    rig.executor._permission._policy.always_deny = ["Probe"]
    by_policy    = await rig.run("c")
    for result in (classifier, by_user, by_policy):
        assert result.is_error and result.content.endswith("[Retry hint from a hook: ask the user for a narrower command]")
    assert [context.extra["source"] for context in seen] == ["classifier", "user", "policy"]
    assert seen[0].tool_name == "Probe" and seen[0].tool_input == {"command": "a"} and seen[0].extra["reason"].startswith("Permission denied")


async def test_a_hook_without_a_hint_leaves_the_refusal_as_it_was(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    engine, _ = _hooks(hint="")
    rig       = _Rig(tmp_path, monkeypatch, "enforce", _Fake("check", DENY_JSON), hooks=engine)
    assert "Retry hint" not in (await rig.run()).content


async def test_a_granted_call_fires_no_denial_hook(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    engine, seen = _hooks()
    rig          = _Rig(tmp_path, monkeypatch, "enforce", _Fake("ok"), hooks=engine)
    await rig.run()
    assert seen == []


# ---------------------------------------------------------------------------
# Cost: the ledger, the session totals and the cost limit
# ---------------------------------------------------------------------------

PRICING = "acme:\n  priced: {input_per_1m: 1000000.0, output_per_1m: 1000000.0}\n  small:  {input_per_1m: 1000000.0, output_per_1m: 1000000.0}\n"


def _limits(tmp_path: Path, max_cost: float = 0.0) -> tuple[RunLimits, SimpleLoop]:
    pricing = tmp_path / "pricing.yml"
    pricing.write_text(PRICING, encoding="utf-8")
    table    = PricingTable(pricing_path=pricing)
    settings = NerdvanaSettings()
    settings.model.provider = "acme"
    settings.model.model    = "priced"
    settings.session.max_cost_usd = max_cost
    limits = RunLimits(settings, Counter(), table, AnalyticsWriter(db_path=tmp_path / "a.sqlite", pricing_table=table))
    limits.analytics_writer.start_session("s")
    return limits, SimpleLoop(limits)


class SimpleLoop:
    """The parts of an AgentLoop the feed reads."""

    def __init__(self, limits: RunLimits) -> None:
        from nerdvana_cli.core.telemetry.analytics import CallOrigin

        self.limits     = limits
        self.origin     = CallOrigin(agent_id="agent-7", agent_type="Explore", category="deep", parent_session_id="p")
        self.turns_used = 4
        self._last_tool = "Bash"
        self.session    = SessionStorage(session_id="s", storage_dir="/nonexistent", persist=False)


def test_the_requests_are_booked_under_the_classifier_for_the_model_they_ran_on(tmp_path: Path) -> None:
    from nerdvana_cli.core.loop.loop_support import classifier_feed

    limits, loop = _limits(tmp_path)
    loop.session.record_user_message("build it")
    feed = classifier_feed(loop)                          # type: ignore[arg-type]
    assert feed.user_prompts() == ["build it"]
    cost = feed.charge(Completion("ok", {"input_tokens": 3, "output_tokens": 1}, "acme", "small"))
    assert cost == pytest.approx(4.0)                     # priced as acme/small, not as the session model
    assert (limits.cost_usd, limits.input_tokens, limits.output_tokens) == (pytest.approx(4.0), 3, 1)
    breakdown = AnalyticsReader(tmp_path / "a.sqlite").cost_breakdown("s")
    assert breakdown["classifier"] == {"requests": 1, "cost_usd": pytest.approx(4.0)}
    with sqlite3.connect(tmp_path / "a.sqlite") as conn:
        row = conn.execute("SELECT agent_id, category, parent_session_id, turn, last_tool, model FROM api_calls").fetchone()
    assert row == ("agent-7", "deep", "p", 4, "Bash", "small")


def test_the_classifier_stops_when_the_cost_limit_is_spent(tmp_path: Path) -> None:
    from nerdvana_cli.core.loop.loop_support import classifier_feed

    limits, loop = _limits(tmp_path, max_cost=5.0)
    feed = classifier_feed(loop)                          # type: ignore[arg-type]
    assert feed.over_limit() is False
    feed.charge(Completion("ok", {"input_tokens": 6}, "acme", "small"))
    assert feed.over_limit() is True and limits.over_cost_limit() != ""


async def test_a_spent_cost_limit_makes_the_classifier_ask_instead_of_calling_the_model(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _Fake("ok")
    rig  = _Rig(tmp_path, monkeypatch, "enforce", fake)
    rig.context.state["classifier_feed"] = ClassifierFeed(lambda: [], lambda answer: 0.0, lambda: True)
    rig.answers = [False]
    assert (await rig.run()).content.startswith("Permission denied by user") and fake.calls == 0


# ---------------------------------------------------------------------------
# Through a whole loop
# ---------------------------------------------------------------------------


class _CallsOnce:
    """A provider that asks for one tool call, then ends the turn."""

    def __init__(self) -> None:
        self.calls = 0

    async def stream(self, system_prompt: str, messages: Any, tools: Any) -> AsyncIterator[ProviderEvent]:
        self.calls += 1
        if self.calls == 1:
            yield ProviderEvent(type="tool_use_complete", tool_use_id="c1", tool_name="Probe", tool_input_complete={"command": "rm -rf build"})
            yield ProviderEvent(type="done", stop_reason="tool_use")
        else:
            yield ProviderEvent(type="content_delta", content="done")
            yield ProviderEvent(type="done", stop_reason="end_turn")


async def test_a_loop_feeds_the_classifier_the_users_prompt_and_pays_for_it_in_its_ledger(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NERDVANA_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setattr(AgentLoop, "create_provider_from_settings", lambda self: _CallsOnce())
    monkeypatch.setattr(AgentLoop, "build_system_prompt", lambda self: "system")
    seen: list[str] = []

    async def fake(system: str, user: str, max_tokens: int) -> Completion:
        seen.append(user)
        answer = "check" if max_tokens < 100 else DENY_JSON
        return Completion(answer, {"input_tokens": 10, "output_tokens": 1}, "acme", "small")

    monkeypatch.setattr("nerdvana_cli.core.safety.classifier.provider_completion", lambda settings: fake)
    pricing = tmp_path / "pricing.yml"
    pricing.write_text(PRICING, encoding="utf-8")
    table    = PricingTable(pricing_path=pricing)
    registry = ToolRegistry()
    tool     = _Tool()
    registry.register(tool)
    settings                        = NerdvanaSettings()
    settings.cwd                    = str(tmp_path)
    settings.model.provider         = "acme"
    settings.model.model            = "priced"
    settings.permissions.classifier = "enforce"
    loop = AgentLoop(
        settings=settings, registry=registry, session=SessionStorage(session_id="loop", storage_dir=str(tmp_path / "sessions")),
        analytics_writer=AnalyticsWriter(db_path=tmp_path / "a.sqlite", pricing_table=table), pricing_table=table,
    )
    output = "".join([chunk async for chunk in loop.run("please clean the build directory")])
    assert tool.ran == 0 and output.endswith("done")
    assert len(seen) == 2 and all("please clean the build directory" in request and "rm -rf build" in request for request in seen)
    assert loop.signal_summary()[CLASSIFIER_DENY] == 1
    breakdown = AnalyticsReader(tmp_path / "a.sqlite").cost_breakdown("loop")
    assert breakdown["classifier"]["requests"] == 2 and breakdown["classifier"]["cost_usd"] == pytest.approx(22.0)
    assert loop.session_cost_usd() >= 22.0
