"""Provider failure classification and recovery planning.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from nerdvana_cli.core import model_failover
from nerdvana_cli.core.agent_loop import AgentLoop
from nerdvana_cli.core.provider_recovery import (
    COMPACT,
    FALLBACK,
    GIVE_UP,
    RESEND,
    RETRY,
    RecoveryPlanner,
    parse_fallback,
)
from nerdvana_cli.core.session import SessionStorage
from nerdvana_cli.core.settings import NerdvanaSettings
from nerdvana_cli.core.tool import ToolRegistry
from nerdvana_cli.providers.base import ProviderEvent
from nerdvana_cli.providers.errors import (
    AUTH,
    CONTEXT_LIMIT,
    DECODE,
    OTHER,
    RETRYABLE,
    ProviderFailure,
    classify_exception,
)

# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


class _HttpError(Exception):
    def __init__(self, message: str, status_code: int, retry_after: str | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.response    = type("R", (), {"headers": {"retry-after": retry_after} if retry_after else {}})()


class RateLimitError(Exception):
    pass


@pytest.mark.parametrize(
    ("exc", "kind"),
    [
        (_HttpError("slow down", 429, "7"), RETRYABLE),
        (_HttpError("overloaded", 529), RETRYABLE),
        (_HttpError("bad key", 401), AUTH),
        (_HttpError("prompt is too long: 250000 tokens", 400), CONTEXT_LIMIT),
        (_HttpError("payload too large", 413), CONTEXT_LIMIT),
        (_HttpError("invalid tool schema", 400), OTHER),
        (RateLimitError("whatever"), RETRYABLE),
        (TimeoutError(), RETRYABLE),
        (UnicodeDecodeError("utf-8", b"\xff", 0, 1, "bad"), DECODE),
        (RuntimeError("HTTP 503 Service Unavailable"), RETRYABLE),
        (RuntimeError("model not found"), OTHER),
    ],
)
def test_classification(exc: Exception, kind: str) -> None:
    assert classify_exception(exc).kind == kind


def test_retry_after_header_is_read_and_capped() -> None:
    assert classify_exception(_HttpError("x", 429, "7")).retry_after == 7.0
    assert classify_exception(_HttpError("x", 429, "9999")).retry_after == 120.0


def test_status_text_is_ignored_when_a_status_is_known() -> None:
    assert classify_exception(_HttpError("upstream said 503", 404)).kind == OTHER


# ---------------------------------------------------------------------------
# Planning
# ---------------------------------------------------------------------------


def test_parse_fallback_recognises_provider_prefix_only() -> None:
    assert parse_fallback("openai:gpt-5") == ("openai", "gpt-5")
    assert parse_fallback("llama3:8b") == (None, "llama3:8b")
    assert parse_fallback("claude-sonnet") == (None, "claude-sonnet")


def test_retryable_retries_then_falls_back_and_the_fallback_gets_its_own_retries() -> None:
    planner = RecoveryPlanner(fallbacks=["b"], max_retries=2, base_delay=0.01)
    failure = ProviderFailure(RETRYABLE)
    current = "a"
    kinds: list[str] = []
    for _ in range(6):
        action = planner.plan(failure, current, "anthropic", streamed=False)
        kinds.append(action.kind)
        if action.kind == FALLBACK:
            current = action.model
    assert kinds == [RETRY, RETRY, FALLBACK, RETRY, RETRY, GIVE_UP]


def test_retry_honours_retry_after() -> None:
    planner = RecoveryPlanner(fallbacks=[], max_retries=1)
    assert planner.plan(ProviderFailure(RETRYABLE, 429, 4.0), "a", "anthropic", streamed=False).delay == 4.0


def test_nothing_is_retried_after_output_was_streamed() -> None:
    planner = RecoveryPlanner(fallbacks=["b"])
    assert planner.plan(ProviderFailure(RETRYABLE), "a", "anthropic", streamed=True).kind == GIVE_UP


def test_context_limit_compacts_once() -> None:
    planner = RecoveryPlanner(fallbacks=[])
    failure = ProviderFailure(CONTEXT_LIMIT)
    assert planner.plan(failure, "a", "x", streamed=False).kind == COMPACT
    assert planner.plan(failure, "a", "x", streamed=False).kind == GIVE_UP


def test_decode_failure_resends_without_streaming() -> None:
    assert RecoveryPlanner(fallbacks=[]).plan(ProviderFailure(DECODE), "a", "x", streamed=False).kind == RESEND


def test_auth_failure_only_falls_back_to_another_provider() -> None:
    same  = RecoveryPlanner(fallbacks=["other-model"])
    cross = RecoveryPlanner(fallbacks=["other-model", "openai:gpt-5"])
    assert same.plan(ProviderFailure(AUTH), "a", "anthropic", streamed=False).kind == GIVE_UP
    action = cross.plan(ProviderFailure(AUTH), "a", "anthropic", streamed=False)
    assert (action.kind, action.provider, action.model) == (FALLBACK, "openai", "gpt-5")


def test_fallback_skips_models_in_cooldown() -> None:
    planner = RecoveryPlanner(fallbacks=["b", "c"], max_retries=0)
    first   = planner.plan(ProviderFailure(RETRYABLE), "a", "p", streamed=False)
    second  = planner.plan(ProviderFailure(RETRYABLE), first.model, "p", streamed=False)
    third   = planner.plan(ProviderFailure(RETRYABLE), second.model, "p", streamed=False)
    assert [first.model, second.model, third.kind] == ["b", "c", GIVE_UP]


# ---------------------------------------------------------------------------
# Agent loop
# ---------------------------------------------------------------------------


class _Scripted:
    """Serves error events first, then a normal answer."""

    def __init__(self, failures: list[ProviderEvent], answer: str = "recovered") -> None:
        self.failures = list(failures)
        self.answer   = answer
        self.calls    = 0
        self.models:  list[str] = []

    async def stream(self, system_prompt: str, messages: Any, tools: Any) -> AsyncIterator[ProviderEvent]:
        self.calls += 1
        if self.failures:
            yield self.failures.pop(0)
            return
        yield ProviderEvent(type="content_delta", content=self.answer)
        yield ProviderEvent(type="done", stop_reason="end_turn")


def _loop(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, provider: _Scripted) -> AgentLoop:
    monkeypatch.setenv("NERDVANA_DATA_HOME", str(tmp_path / "data"))

    def _create(self: AgentLoop) -> _Scripted:
        provider.models.append(f"{self.settings.model.provider}:{self.settings.model.model}")
        return provider

    async def _no_sleep(_delay: float) -> None:
        return None

    monkeypatch.setattr(AgentLoop, "create_provider_from_settings", _create)
    monkeypatch.setattr(AgentLoop, "build_system_prompt", lambda self: "system")
    monkeypatch.setattr(model_failover.asyncio, "sleep", _no_sleep)
    settings                = NerdvanaSettings()
    settings.cwd            = str(tmp_path)
    settings.model.provider = "anthropic"
    settings.model.model    = "primary"
    return AgentLoop(
        settings = settings,
        registry = ToolRegistry(),
        session  = SessionStorage(session_id="recover", storage_dir=str(tmp_path / "sessions")),
    )


def _error(kind: str) -> ProviderEvent:
    return ProviderEvent(type="error", error=f"{kind} failure", error_kind=kind)


async def test_error_event_is_retried_on_the_same_model(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    provider = _Scripted([_error(RETRYABLE)])
    loop     = _loop(monkeypatch, tmp_path, provider)

    output = "".join([c async for c in loop.run("hi")])

    assert "Retrying" in output
    assert "recovered" in output
    assert provider.calls == 2


async def test_error_event_reaches_the_fallback_model(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    provider = _Scripted([_error(RETRYABLE)])
    loop     = _loop(monkeypatch, tmp_path, provider)
    loop.settings.model.max_retries     = 0
    loop.settings.model.fallback_models = ["secondary"]

    output = "".join([c async for c in loop.run("hi")])

    assert "Fallback" in output
    assert "recovered" in output
    assert "anthropic:secondary" in provider.models
    assert loop.settings.model.model == "primary"


async def test_cross_provider_fallback_restores_the_original_provider(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    provider = _Scripted([_error(RETRYABLE)])
    loop     = _loop(monkeypatch, tmp_path, provider)
    loop.settings.model.max_retries     = 0
    loop.settings.model.fallback_models = ["openai:gpt-backup"]
    original_key = loop.settings.model.api_key

    async for _ in loop.run("hi"):
        pass

    assert "openai:gpt-backup" in provider.models
    assert loop.settings.model.provider == "anthropic"
    assert loop.settings.model.api_key == original_key


async def test_unrecoverable_error_event_is_reported(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    provider = _Scripted([_error(OTHER)])
    loop     = _loop(monkeypatch, tmp_path, provider)

    output = "".join([c async for c in loop.run("hi")])

    assert "Provider error" in output
    assert provider.calls == 1


async def test_context_limit_compacts_and_retries(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    provider  = _Scripted([_error(CONTEXT_LIMIT)])
    loop      = _loop(monkeypatch, tmp_path, provider)
    compacted: list[int] = []

    async def _compact(self: AgentLoop, cur_toks: int, thr: int) -> AsyncIterator[str]:
        compacted.append(thr)
        if False:
            yield ""

    monkeypatch.setattr(AgentLoop, "_maybe_compact_messages", _compact)

    output = "".join([c async for c in loop.run("hi")])

    assert len(compacted) == 1
    assert "recovered" in output
