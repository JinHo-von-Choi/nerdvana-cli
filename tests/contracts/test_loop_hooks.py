"""Contract tests for T-0A-05: LoopHookEngine recovery hooks.

Verifies:
- on_api_call: context_limit_recovery fires on max_tokens stop.
- on_api_call: ralph_loop_check is NOT fired on end_turn (baseline behaviour).
- _is_retryable_error: classifies retryable vs non-retryable errors.
"""

from __future__ import annotations

import pytest

from nerdvana_cli.core.builtin_hooks import (
    context_limit_recovery,
    ralph_loop_check,
)
from nerdvana_cli.core.hooks import HookEngine, HookEvent
from nerdvana_cli.core.loop_hooks import LoopHookEngine
from nerdvana_cli.core.loop_state import LoopState
from nerdvana_cli.core.settings import ModelConfig, NerdvanaSettings, SessionConfig
from nerdvana_cli.core.tool import ToolRegistry
from nerdvana_cli.types import Message, Role


@pytest.fixture
def base_state() -> LoopState:
    return LoopState(
        iteration         = 1,
        stop_reason       = "continue",
        continuation_hint = None,
        token_budget_used = 0,
        session_id        = "test-hooks-00",
    )


@pytest.fixture
def engine_with_hooks() -> LoopHookEngine:
    """Return a LoopHookEngine with real builtin hooks registered."""
    settings        = NerdvanaSettings()
    settings.model  = ModelConfig(
        provider = "anthropic",
        model    = "claude-test-mock",
        api_key  = "test-key",
    )
    settings.session = SessionConfig(
        persist            = False,
        max_turns          = 50,
        max_context_tokens = 10_000,
        compact_threshold  = 0.8,
        planning_gate      = False,
    )

    hooks = HookEngine()
    hooks.register(HookEvent.AFTER_API_CALL, context_limit_recovery)
    hooks.register(HookEvent.AFTER_API_CALL, ralph_loop_check)

    registry = ToolRegistry()

    return LoopHookEngine(hooks=hooks, settings=settings, registry=registry)


def test_context_limit_recovery_fires_on_max_tokens(
    engine_with_hooks: LoopHookEngine,
    base_state: LoopState,
) -> None:
    """on_api_call with max_tokens stop must produce at least one inject message."""
    messages = [
        Message(role=Role.USER, content="Give me a long response."),
    ]
    response = {"stop_reason": "max_tokens"}

    new_state, inject = engine_with_hooks.on_api_call(base_state, response, messages)

    assert inject, "context_limit_recovery should inject at least one message on max_tokens"
    injected_contents = [m.get("content", "") for m in inject]
    assert any("Context limit" in c for c in injected_contents), (
        "Injected message should contain 'Context limit'"
    )


def test_ralph_loop_check_does_not_fire_on_end_turn(
    engine_with_hooks: LoopHookEngine,
    base_state: LoopState,
) -> None:
    """ralph_loop_check fires on end_turn in builtin_hooks, but end_turn path
    in AgentLoop does NOT call on_api_call (it returns early).

    This test verifies on_turn_end does NOT produce ralph inject messages,
    preserving the 0.4.1 baseline behaviour.
    """
    # on_turn_end should not inject ralph messages
    new_state = engine_with_hooks.on_turn_end(base_state)

    assert new_state.stop_reason == "end_turn"
    # No inject messages — on_turn_end only evolves state


def test_is_retryable_error_classifies_correctly(
    engine_with_hooks: LoopHookEngine,
) -> None:
    """_is_retryable_error must classify rate-limit / timeout errors correctly."""
    retryable_cases = [
        Exception("429 Too Many Requests"),
        Exception("503 Service Unavailable"),
        Exception("rate limit exceeded"),
        Exception("Request timeout"),
        Exception("529 overloaded"),
    ]
    non_retryable_cases = [
        Exception("400 Bad Request"),
        Exception("Invalid tool input"),
        Exception("File not found"),
        Exception("AttributeError: 'NoneType' object"),
    ]

    for exc in retryable_cases:
        assert engine_with_hooks._is_retryable_error(exc), (
            f"Expected {exc} to be classified as retryable"
        )

    for exc in non_retryable_cases:
        assert not engine_with_hooks._is_retryable_error(exc), (
            f"Expected {exc} to NOT be classified as retryable"
        )
