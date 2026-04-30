"""Unit tests for activity_hooks — four built-in hook handlers and AgentLoop integration."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from nerdvana_cli.core.activity_hooks import (
    make_after_api_call_handler,
    make_after_tool_handler,
    make_before_api_call_handler,
    make_before_tool_handler,
)
from nerdvana_cli.core.activity_state import ActivityState, summarize_tool_call
from nerdvana_cli.core.agent_loop import AgentLoop
from nerdvana_cli.core.hooks import HookContext, HookEvent
from nerdvana_cli.core.settings import ModelConfig, NerdvanaSettings, SessionConfig
from nerdvana_cli.core.tool import ToolRegistry

# ---------------------------------------------------------------------------
# Shared fixtures / helpers
# ---------------------------------------------------------------------------


def _make_settings(provider: str = "anthropic", model: str = "test-model") -> NerdvanaSettings:
    settings = MagicMock(spec=NerdvanaSettings)
    settings.model   = ModelConfig(provider=provider, model=model, api_key="test-key")
    settings.session = SessionConfig()
    settings.cwd     = "/tmp"
    settings.verbose = False
    return settings


def _make_loop(provider_name: str = "anthropic", model: str = "test-model") -> AgentLoop:
    settings = _make_settings(provider=provider_name, model=model)
    registry = ToolRegistry()
    with (
        patch.object(AgentLoop, "create_provider_from_settings", return_value=MagicMock()),
    ):
        loop = AgentLoop(settings=settings, registry=registry)
    return loop


def _make_minimal_loop() -> AgentLoop:
    """Return an AgentLoop whose activity_state can be mutated by handlers."""
    return _make_loop()


# ---------------------------------------------------------------------------
# 1. BEFORE_API_CALL → phase="waiting_api", label contains provider name
# ---------------------------------------------------------------------------


class TestBeforeApiCallHandler:
    def test_phase_set_to_waiting_api(self) -> None:
        loop    = _make_minimal_loop()
        handler = make_before_api_call_handler(loop)
        ctx     = HookContext(event=HookEvent.BEFORE_API_CALL, settings=loop.settings)

        result = handler(ctx)

        assert loop.activity_state.phase == "waiting_api"
        assert result is None

    def test_label_contains_provider_name(self) -> None:
        loop    = _make_loop(provider_name="anthropic")
        handler = make_before_api_call_handler(loop)
        ctx     = HookContext(event=HookEvent.BEFORE_API_CALL, settings=loop.settings)

        handler(ctx)

        assert "anthropic" in loop.activity_state.label

    def test_label_fallback_when_provider_empty(self) -> None:
        loop                        = _make_minimal_loop()
        loop.settings.model.provider = ""
        handler                     = make_before_api_call_handler(loop)
        ctx                         = HookContext(event=HookEvent.BEFORE_API_CALL, settings=loop.settings)

        handler(ctx)

        assert "provider" in loop.activity_state.label

    def test_started_at_set(self) -> None:
        loop    = _make_minimal_loop()
        handler = make_before_api_call_handler(loop)
        ctx     = HookContext(event=HookEvent.BEFORE_API_CALL, settings=loop.settings)

        handler(ctx)

        assert loop.activity_state.started_at is not None
        assert loop.activity_state.started_at > 0

    def test_detail_and_tool_name_cleared(self) -> None:
        loop                           = _make_minimal_loop()
        loop.activity_state.detail     = "leftover"
        loop.activity_state.tool_name  = "OldTool"
        handler                        = make_before_api_call_handler(loop)
        ctx                            = HookContext(event=HookEvent.BEFORE_API_CALL, settings=loop.settings)

        handler(ctx)

        assert loop.activity_state.detail    == ""
        assert loop.activity_state.tool_name == ""


# ---------------------------------------------------------------------------
# 2. BEFORE_TOOL → phase="tool_running", label/detail from summarize_tool_call
# ---------------------------------------------------------------------------


class TestBeforeToolHandler:
    def test_phase_set_to_tool_running(self) -> None:
        loop    = _make_minimal_loop()
        handler = make_before_tool_handler(loop)
        ctx     = HookContext(
            event      = HookEvent.BEFORE_TOOL,
            tool_name  = "Bash",
            tool_input = {"command": "ls -la"},
        )

        handler(ctx)

        assert loop.activity_state.phase == "tool_running"

    def test_label_and_detail_match_summarize(self) -> None:
        loop    = _make_minimal_loop()
        handler = make_before_tool_handler(loop)
        ctx     = HookContext(
            event      = HookEvent.BEFORE_TOOL,
            tool_name  = "Bash",
            tool_input = {"command": "ls -la"},
        )

        handler(ctx)

        expected_label, expected_detail = summarize_tool_call("Bash", {"command": "ls -la"})
        assert loop.activity_state.label  == expected_label
        assert loop.activity_state.detail == expected_detail

    def test_tool_name_stored(self) -> None:
        loop    = _make_minimal_loop()
        handler = make_before_tool_handler(loop)
        ctx     = HookContext(
            event      = HookEvent.BEFORE_TOOL,
            tool_name  = "FileRead",
            tool_input = {"file_path": "/some/file.py"},
        )

        handler(ctx)

        assert loop.activity_state.tool_name == "FileRead"

    def test_started_at_set(self) -> None:
        loop    = _make_minimal_loop()
        handler = make_before_tool_handler(loop)
        ctx     = HookContext(event=HookEvent.BEFORE_TOOL, tool_name="Bash", tool_input={})

        handler(ctx)

        assert loop.activity_state.started_at is not None

    def test_empty_tool_input(self) -> None:
        loop    = _make_minimal_loop()
        handler = make_before_tool_handler(loop)
        ctx     = HookContext(event=HookEvent.BEFORE_TOOL, tool_name="UnknownTool", tool_input={})

        handler(ctx)

        assert loop.activity_state.phase     == "tool_running"
        assert loop.activity_state.tool_name == "UnknownTool"

    def test_none_tool_input_treated_as_empty(self) -> None:
        loop    = _make_minimal_loop()
        handler = make_before_tool_handler(loop)
        ctx     = HookContext(event=HookEvent.BEFORE_TOOL, tool_name="Bash", tool_input=None)  # type: ignore[arg-type]

        handler(ctx)

        assert loop.activity_state.phase == "tool_running"


# ---------------------------------------------------------------------------
# 3. AFTER_TOOL → phase="streaming"
# ---------------------------------------------------------------------------


class TestAfterToolHandler:
    def test_phase_set_to_streaming(self) -> None:
        loop    = _make_minimal_loop()
        handler = make_after_tool_handler(loop)
        ctx     = HookContext(event=HookEvent.AFTER_TOOL)

        handler(ctx)

        assert loop.activity_state.phase == "streaming"

    def test_label_contains_model_name(self) -> None:
        loop    = _make_loop(model="test-model-x")
        handler = make_after_tool_handler(loop)
        ctx     = HookContext(event=HookEvent.AFTER_TOOL)

        handler(ctx)

        assert "test-model-x" in loop.activity_state.label

    def test_detail_and_tool_cleared(self) -> None:
        loop                           = _make_minimal_loop()
        loop.activity_state.detail     = "stale detail"
        loop.activity_state.tool_name  = "OldTool"
        handler                        = make_after_tool_handler(loop)
        ctx                            = HookContext(event=HookEvent.AFTER_TOOL)

        handler(ctx)

        assert loop.activity_state.detail    == ""
        assert loop.activity_state.tool_name == ""

    def test_returns_none(self) -> None:
        loop    = _make_minimal_loop()
        handler = make_after_tool_handler(loop)
        result  = handler(HookContext(event=HookEvent.AFTER_TOOL))

        assert result is None


# ---------------------------------------------------------------------------
# 4. AFTER_API_CALL with stop_reason="end_turn" → phase="idle"
# ---------------------------------------------------------------------------


class TestAfterApiCallHandler:
    def test_end_turn_sets_idle(self) -> None:
        loop                       = _make_minimal_loop()
        loop.activity_state.phase  = "streaming"
        handler                    = make_after_api_call_handler(loop)
        ctx                        = HookContext(event=HookEvent.AFTER_API_CALL, stop_reason="end_turn")

        handler(ctx)

        assert loop.activity_state.phase == "idle"
        assert loop.activity_state.label == "Ready"

    def test_max_tokens_sets_idle(self) -> None:
        loop                       = _make_minimal_loop()
        loop.activity_state.phase  = "streaming"
        handler                    = make_after_api_call_handler(loop)
        ctx                        = HookContext(event=HookEvent.AFTER_API_CALL, stop_reason="max_tokens")

        handler(ctx)

        assert loop.activity_state.phase == "idle"

    def test_tool_use_does_not_set_idle(self) -> None:
        loop                       = _make_minimal_loop()
        loop.activity_state.phase  = "streaming"
        handler                    = make_after_api_call_handler(loop)
        ctx                        = HookContext(event=HookEvent.AFTER_API_CALL, stop_reason="tool_use")

        handler(ctx)

        assert loop.activity_state.phase == "streaming"

    def test_none_stop_reason_does_not_set_idle(self) -> None:
        loop                       = _make_minimal_loop()
        loop.activity_state.phase  = "streaming"
        handler                    = make_after_api_call_handler(loop)
        ctx                        = HookContext(event=HookEvent.AFTER_API_CALL, stop_reason=None)

        handler(ctx)

        assert loop.activity_state.phase == "streaming"

    def test_end_turn_clears_detail_and_tool_name(self) -> None:
        loop                           = _make_minimal_loop()
        loop.activity_state.detail     = "leftover"
        loop.activity_state.tool_name  = "SomeTool"
        handler                        = make_after_api_call_handler(loop)
        ctx                            = HookContext(event=HookEvent.AFTER_API_CALL, stop_reason="end_turn")

        handler(ctx)

        assert loop.activity_state.detail    == ""
        assert loop.activity_state.tool_name == ""

    def test_returns_none(self) -> None:
        loop    = _make_minimal_loop()
        handler = make_after_api_call_handler(loop)
        result  = handler(HookContext(event=HookEvent.AFTER_API_CALL, stop_reason="end_turn"))

        assert result is None


# ---------------------------------------------------------------------------
# 5. on_activity_change callback invocation counting
# ---------------------------------------------------------------------------


class TestOnActivityChangeCallback:
    def test_callback_invoked_on_phase_change(self) -> None:
        call_log: list[ActivityState] = []
        settings  = _make_settings()
        registry  = ToolRegistry()

        with patch.object(AgentLoop, "create_provider_from_settings", return_value=MagicMock()):
            loop = AgentLoop(
                settings           = settings,
                registry           = registry,
                on_activity_change = lambda s: call_log.append(ActivityState(
                    phase      = s.phase,
                    label      = s.label,
                    detail     = s.detail,
                    tool_name  = s.tool_name,
                    started_at = s.started_at,
                )),
            )

        before_count = len(call_log)
        loop._set_activity(phase="thinking", label="Thinking...")
        assert len(call_log) > before_count
        assert call_log[-1].phase == "thinking"

    def test_callback_receives_correct_state(self) -> None:
        received: list[ActivityState] = []
        settings  = _make_settings()
        registry  = ToolRegistry()

        with patch.object(AgentLoop, "create_provider_from_settings", return_value=MagicMock()):
            loop = AgentLoop(
                settings           = settings,
                registry           = registry,
                on_activity_change = lambda s: received.append(ActivityState(
                    phase      = s.phase,
                    label      = s.label,
                    detail     = s.detail,
                    tool_name  = s.tool_name,
                    started_at = s.started_at,
                )),
            )

        loop._set_activity(phase="tool_running", label="Bash", detail="ls -la", tool_name="Bash")

        last = received[-1]
        assert last.phase     == "tool_running"
        assert last.label     == "Bash"
        assert last.detail    == "ls -la"
        assert last.tool_name == "Bash"

    def test_multiple_set_activity_calls_each_trigger_callback(self) -> None:
        call_log: list[str] = []
        settings  = _make_settings()
        registry  = ToolRegistry()

        with patch.object(AgentLoop, "create_provider_from_settings", return_value=MagicMock()):
            loop = AgentLoop(
                settings           = settings,
                registry           = registry,
                on_activity_change = lambda s: call_log.append(s.phase),
            )

        initial_count = len(call_log)
        loop._set_activity(phase="thinking",    label="Thinking...")
        loop._set_activity(phase="tool_running", label="Bash", tool_name="Bash")
        loop._set_activity(phase="idle",         label="Ready")

        new_calls = call_log[initial_count:]
        assert "thinking"     in new_calls
        assert "tool_running" in new_calls
        assert "idle"         in new_calls


# ---------------------------------------------------------------------------
# 6. Callback exception must not propagate (loop safety)
# ---------------------------------------------------------------------------


class TestCallbackExceptionSafety:
    def test_exception_in_callback_does_not_crash_loop(self) -> None:
        def bad_callback(state: ActivityState) -> None:
            raise RuntimeError("callback failure")

        settings = _make_settings()
        registry = ToolRegistry()

        with patch.object(AgentLoop, "create_provider_from_settings", return_value=MagicMock()):
            loop = AgentLoop(
                settings           = settings,
                registry           = registry,
                on_activity_change = bad_callback,
            )

        loop._set_activity(phase="thinking", label="Thinking...")
        assert loop.activity_state.phase == "thinking"

    def test_activity_state_mutated_even_when_callback_raises(self) -> None:
        def bad_callback(state: ActivityState) -> None:
            raise ValueError("oops")

        settings = _make_settings()
        registry = ToolRegistry()

        with patch.object(AgentLoop, "create_provider_from_settings", return_value=MagicMock()):
            loop = AgentLoop(
                settings           = settings,
                registry           = registry,
                on_activity_change = bad_callback,
            )

        loop._set_activity(phase="streaming", label="Streaming from test-model")
        assert loop.activity_state.phase == "streaming"
        assert loop.activity_state.label == "Streaming from test-model"


# ---------------------------------------------------------------------------
# 7. register_activity_hooks wires all four events
# ---------------------------------------------------------------------------


class TestRegisterActivityHooks:
    def test_all_four_events_registered(self) -> None:
        loop = _make_minimal_loop()

        for event in (
            HookEvent.BEFORE_API_CALL,
            HookEvent.BEFORE_TOOL,
            HookEvent.AFTER_TOOL,
            HookEvent.AFTER_API_CALL,
        ):
            assert loop.hooks.has_handlers(event), f"No handler registered for {event}"

    def test_activity_state_initialized_idle(self) -> None:
        loop = _make_minimal_loop()
        assert loop.activity_state.phase == "idle"
        assert loop.activity_state.label == "Ready"

    def test_no_on_activity_change_by_default(self) -> None:
        loop = _make_minimal_loop()
        assert loop._on_activity_change is None

        loop._set_activity(phase="thinking", label="Thinking...")
        assert loop.activity_state.phase == "thinking"
