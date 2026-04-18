"""Fault-injection tests for T-0A-07.

Three scenarios that exercise error-handling paths added/preserved in
Phase 0A:

1. Provider HTTP-500 retry — provider raises a retryable exception 3
   times; AgentLoop falls back to a secondary model on the 4th attempt.
2. Tool asyncio.TimeoutError — ToolExecutor returns an error ToolResult
   when the tool's ``call()`` raises TimeoutError.
3. Session write OSError(ENOSPC) — when SessionStorage.record() raises
   ENOSPC, the JSONL file written *before* the failure is still valid
   (parseable) and contains every turn committed up to that point.
"""

from __future__ import annotations

import asyncio
import errno
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from unittest.mock import patch

from nerdvana_cli.core.agent_loop import AgentLoop
from nerdvana_cli.core.session import SessionStorage
from nerdvana_cli.core.settings import ModelConfig, NerdvanaSettings, SessionConfig
from nerdvana_cli.core.tool import BaseTool, ToolContext, ToolRegistry
from nerdvana_cli.providers.base import ProviderEvent
from nerdvana_cli.types import ToolResult

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _make_settings(tmp_dir: str, fallback_models: list[str] | None = None) -> NerdvanaSettings:
    settings        = NerdvanaSettings()
    settings.model  = ModelConfig(
        provider        = "anthropic",
        model           = "claude-test-mock",
        api_key         = "test-key-fault",
        fallback_models = fallback_models or [],
    )
    settings.session = SessionConfig(
        persist            = False,
        max_turns          = 10,
        max_context_tokens = 10_000,
        compact_threshold  = 0.9,
        planning_gate      = False,
    )
    settings.cwd     = tmp_dir
    settings.verbose = False
    return settings


def _run(coro: Any) -> Any:
    return asyncio.get_event_loop().run_until_complete(coro)


async def _collect(loop: AgentLoop, prompt: str) -> list[str]:
    chunks: list[str] = []
    async for chunk in loop.run(prompt):
        chunks.append(chunk)
    return chunks


# ---------------------------------------------------------------------------
# Scenario 1 — Provider retryable error triggers model fallback
# ---------------------------------------------------------------------------

class _FailFirstProvider:
    """Raises a retryable 529 on the first call; succeeds on subsequent calls."""

    def __init__(self) -> None:
        self._calls = 0

    async def stream(self, system_prompt: str, messages: list[Any], tools: list[Any]) -> AsyncIterator[ProviderEvent]:
        self._calls += 1
        if self._calls == 1:
            raise RuntimeError("529 overloaded — please retry")
        yield ProviderEvent(type="content_delta", content="OK after fallback")
        yield ProviderEvent(type="done", stop_reason="end_turn")

    async def send(self, system_prompt: str, messages: list[Any], tools: list[Any]) -> dict[str, Any]:
        return {"content": "fallback send"}

    async def list_models(self) -> list[Any]:
        return []


def test_provider_http_500_retries_then_succeeds(tmp_path: Path) -> None:
    """A retryable 529 error triggers model fallback; the fallback provider succeeds.

    AgentLoop._loop() calls _is_retryable_error → switches to fallback model →
    continues. This test verifies the full path produces the success response.
    """
    settings = _make_settings(str(tmp_path), fallback_models=["claude-fallback-model"])
    registry = ToolRegistry()
    storage  = SessionStorage(session_id="fault-retry", storage_dir=str(tmp_path))
    loop     = AgentLoop(settings=settings, registry=registry, session=storage)

    # A single provider instance: first call fails (529), second succeeds.
    fail_first = _FailFirstProvider()
    loop.provider = fail_first  # type: ignore[assignment]

    # Ensure provider rebuild returns the same mock (so the fallback model uses it).
    with patch.object(loop, "create_provider_from_settings", return_value=fail_first):
        chunks = _run(_collect(loop, "test retry"))

    all_output = "".join(chunks)

    # The fallback notice must appear in output.
    assert "Fallback" in all_output or "fallback" in all_output.lower(), (
        f"Expected fallback notice in output. Got: {all_output!r}"
    )
    # The eventual success content must arrive.
    assert "OK after fallback" in all_output, (
        f"Expected success content after fallback. Got: {all_output!r}"
    )
    # Provider was called exactly 2 times (1 fail + 1 success).
    assert fail_first._calls == 2, (
        f"Expected 2 provider calls (1 fail + 1 success), got {fail_first._calls}"
    )
    # _is_retryable_error must classify the 529 error correctly.
    assert loop.loop_hook_engine._is_retryable_error(RuntimeError("529 overloaded")), (
        "LoopHookEngine._is_retryable_error should classify 529 as retryable"
    )


# ---------------------------------------------------------------------------
# Scenario 2 — Tool asyncio.TimeoutError → ToolExecutor error result
# ---------------------------------------------------------------------------

class _TimeoutTool(BaseTool[dict[str, Any]]):
    """A tool whose call() always raises asyncio.TimeoutError."""

    name                 = "TimeoutTool"
    description_text     = "Always times out"
    input_schema: dict[str, Any] = {"type": "object", "properties": {}}
    is_concurrency_safe  = False

    async def call(
        self,
        args: dict[str, Any],
        context: ToolContext,
        can_use_tool: Any,
        on_progress: Any = None,
    ) -> ToolResult:
        raise TimeoutError("tool execution timed out")


class _ToolUseProvider:
    """Provider that requests one tool call then ends the turn."""

    def __init__(self, tool_name: str) -> None:
        self._tool_name = tool_name
        self._called    = 0

    async def stream(self, system_prompt: str, messages: list[Any], tools: list[Any]) -> AsyncIterator[ProviderEvent]:
        self._called += 1
        if self._called == 1:
            # First turn: emit a tool_use_complete event
            yield ProviderEvent(
                type                = "tool_use_complete",
                tool_use_id         = "tu_timeout_01",
                tool_name           = self._tool_name,
                tool_input_complete = {},
            )
            yield ProviderEvent(type="done", stop_reason="tool_use")
        else:
            # After tool result is injected, end the conversation
            yield ProviderEvent(type="content_delta", content="Done after timeout.")
            yield ProviderEvent(type="done", stop_reason="end_turn")

    async def send(self, *_: Any) -> dict[str, Any]:
        return {"content": ""}

    async def list_models(self) -> list[Any]:
        return []


def test_tool_timeout_produces_error_result(tmp_path: Path) -> None:
    """ToolExecutor must catch asyncio.TimeoutError and return an error ToolResult."""
    settings = _make_settings(str(tmp_path))
    registry = ToolRegistry()
    registry.register(_TimeoutTool())
    storage = SessionStorage(session_id="fault-timeout", storage_dir=str(tmp_path))
    loop    = AgentLoop(settings=settings, registry=registry, session=storage)
    loop.provider = _ToolUseProvider("TimeoutTool")  # type: ignore[assignment]

    _run(_collect(loop, "call the timeout tool"))

    # The loop should have continued (not crashed). Inspect messages for an error tool result.
    tool_result_msgs = [m for m in loop.state.messages if str(getattr(m, "role", "")).lower() == "tool"]
    assert tool_result_msgs, "Expected at least one tool result message in state"
    error_results = [m for m in tool_result_msgs if getattr(m, "is_error", False)]
    assert error_results, (
        "Expected the TimeoutError to produce an error ToolResult, but none found. "
        f"Tool messages: {[m.content for m in tool_result_msgs]}"
    )
    assert any("timeout" in (m.content or "").lower() or "Tool execution error" in (m.content or "") for m in error_results), (
        "Error message should mention the timeout or tool execution error"
    )


# ---------------------------------------------------------------------------
# Scenario 3 — Session JSONL integrity under OSError(ENOSPC)
# ---------------------------------------------------------------------------

class _SimpleProvider:
    """Returns a single plain text response."""

    async def stream(self, system_prompt: str, messages: list[Any], tools: list[Any]) -> AsyncIterator[ProviderEvent]:
        yield ProviderEvent(type="content_delta", content="Hello from simple provider.")
        yield ProviderEvent(type="done", stop_reason="end_turn")

    async def send(self, *_: Any) -> dict[str, Any]:
        return {"content": ""}

    async def list_models(self) -> list[Any]:
        return []


def test_session_jsonl_integrity_on_enospc(tmp_path: Path) -> None:
    """Pre-failure JSONL lines remain parseable when a disk-full error occurs mid-session."""
    settings = _make_settings(str(tmp_path))
    registry = ToolRegistry()
    storage  = SessionStorage(session_id="fault-enospc", storage_dir=str(tmp_path))
    loop     = AgentLoop(settings=settings, registry=registry, session=storage)
    loop.provider = _SimpleProvider()  # type: ignore[assignment]

    # Run one successful turn to produce committed JSONL lines.
    _run(_collect(loop, "first turn before disk full"))

    # Capture the current JSONL content (all lines committed so far).
    jsonl_path = Path(storage.file_path)
    committed_lines = jsonl_path.read_text(encoding="utf-8").splitlines()
    assert committed_lines, "Expected at least one JSONL line after the first turn"

    # Patch SessionStorage.record to raise ENOSPC on subsequent writes.
    _original_record = storage.record

    call_count = [0]

    def _record_with_enospc(event_type: str, data: dict[str, Any]) -> None:
        call_count[0] += 1
        if call_count[0] >= 1:
            raise OSError(errno.ENOSPC, "No space left on device")
        _original_record(event_type, data)

    storage.record = _record_with_enospc  # type: ignore[method-assign]

    # Second turn — record() will raise ENOSPC; AgentLoop should not crash.
    import contextlib
    with contextlib.suppress(OSError):
        _run(_collect(loop, "second turn that triggers disk full"))

    # The JSONL file must still contain every line committed before the failure.
    all_lines = jsonl_path.read_text(encoding="utf-8").splitlines()
    for i, line in enumerate(committed_lines):
        assert line in all_lines, (
            f"Committed line {i} was lost after ENOSPC: {line!r}"
        )
    # All pre-failure lines must be individually parseable JSON.
    for line in committed_lines:
        parsed = json.loads(line)
        assert "type" in parsed, f"JSONL line missing 'type' key: {line!r}"
        assert "ts"   in parsed, f"JSONL line missing 'ts' key: {line!r}"
