"""Parity test fixtures: ProviderMock, AgentLoop factory, message normalizer."""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from nerdvana_cli.core.agent_loop import AgentLoop
from nerdvana_cli.core.session import SessionStorage
from nerdvana_cli.core.settings import ModelConfig, NerdvanaSettings, SessionConfig
from nerdvana_cli.core.tool import ToolRegistry
from nerdvana_cli.providers.base import ModelInfo, ProviderEvent

FIXTURES_DIR = Path(__file__).parent / "fixtures"

_UUID_PATTERN   = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.IGNORECASE)
_SHORT_ID_PATTERN = re.compile(r"\b[0-9a-f]{8}\b")
_ISO_TS_PATTERN = re.compile(
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?"
)
# Matches pytest tmp_path directories, e.g. /tmp/pytest-of-nirna/pytest-123/test_foo0
_PYTEST_TMP_PATTERN = re.compile(r"/tmp/pytest-[^/]+/pytest-\d+/[^\s\"\\<>]+\d+")


class ProviderMock:
    """Deterministic provider mock.

    Loads scripted responses from a fixture JSON file. Each call to
    ``stream()`` pops the next response from the queue and yields its
    events. ``send()`` pops from the ``send_responses`` queue.
    """

    def __init__(self, fixture_path: Path) -> None:
        data                 = json.loads(fixture_path.read_text(encoding="utf-8"))
        self._responses:      list[dict[str, Any]] = list(data.get("responses", []))
        self._send_responses: list[dict[str, Any]] = list(data.get("send_responses", []))
        self._call_index     = 0

    async def stream(
        self,
        system_prompt: str,
        messages:      list[dict[str, Any]],
        tools:         list[Any],
    ) -> AsyncIterator[ProviderEvent]:
        if not self._responses:
            yield ProviderEvent(type="done", stop_reason="end_turn")
            return

        response = self._responses.pop(0)
        self._call_index += 1

        for ev in response.get("events", []):
            event_type = ev["type"]

            if event_type == "content_delta":
                yield ProviderEvent(type="content_delta", content=ev.get("content", ""))

            elif event_type == "tool_use_start":
                yield ProviderEvent(
                    type        = "tool_use_start",
                    tool_use_id = ev.get("tool_use_id", f"tool_{self._call_index:02d}"),
                    tool_name   = ev.get("tool_name", ""),
                )

            elif event_type == "tool_use_delta":
                yield ProviderEvent(
                    type             = "tool_use_delta",
                    tool_input_delta = ev.get("tool_input_delta", ""),
                )

            elif event_type == "tool_use_complete":
                yield ProviderEvent(
                    type                = "tool_use_complete",
                    tool_use_id         = ev.get("tool_use_id", f"tool_{self._call_index:02d}"),
                    tool_name           = ev.get("tool_name", ""),
                    tool_input_complete = ev.get("tool_input_complete", {}),
                )

            elif event_type == "done":
                yield ProviderEvent(
                    type        = "done",
                    stop_reason = ev.get("stop_reason", "end_turn"),
                )

            elif event_type == "usage":
                yield ProviderEvent(
                    type  = "usage",
                    usage = ev.get("usage", {}),
                )

    async def send(
        self,
        system_prompt: str,
        messages:      list[dict[str, Any]],
        tools:         list[Any],
    ) -> dict[str, Any]:
        if self._send_responses:
            return self._send_responses.pop(0)
        return {"content": "<summary>Mock compaction summary.</summary>"}

    async def list_models(self) -> list[ModelInfo]:
        return []


def _make_settings(session_dir: str, extra_session: dict[str, Any] | None = None) -> NerdvanaSettings:
    """Build minimal NerdvanaSettings for testing (no real API key needed)."""
    session_kwargs: dict[str, Any] = {
        "persist":           False,
        "max_turns":         50,
        "max_context_tokens": 10_000,
        "compact_threshold": 0.8,
        "planning_gate":     False,
    }
    if extra_session:
        session_kwargs.update(extra_session)

    settings        = NerdvanaSettings()
    settings.model  = ModelConfig(
        provider = "anthropic",
        model    = "claude-test-mock",
        api_key  = "test-key-00000000",
    )
    settings.session = SessionConfig(**session_kwargs)
    settings.cwd     = session_dir
    settings.verbose = False
    return settings


def _make_minimal_registry() -> ToolRegistry:
    """Return a ToolRegistry with standard read/search tools only."""
    from nerdvana_cli.tools.file_tools import FileReadTool
    from nerdvana_cli.tools.search_tools import GlobTool, GrepTool

    registry = ToolRegistry()
    registry.register(FileReadTool())
    registry.register(GlobTool())
    registry.register(GrepTool())
    return registry


def _inject_mock_provider(loop: AgentLoop, mock: ProviderMock) -> None:
    """Replace loop.provider with ProviderMock after construction."""
    loop.provider = mock  # type: ignore[assignment]


def _build_agent_loop(
    session_dir: str,
    fixture_name: str,
    extra_session: dict[str, Any] | None = None,
) -> tuple[AgentLoop, ProviderMock]:
    """Construct an AgentLoop with ProviderMock injected."""
    settings = _make_settings(session_dir, extra_session)
    registry = _make_minimal_registry()
    storage  = SessionStorage(session_id="parity-test", storage_dir=session_dir)

    loop = AgentLoop(settings=settings, registry=registry, session=storage)

    fixture_path = FIXTURES_DIR / fixture_name
    mock         = ProviderMock(fixture_path)
    _inject_mock_provider(loop, mock)

    return loop, mock


@pytest.fixture
def session_dir(tmp_path: Path) -> str:
    """Temporary directory for session JSONL files."""
    return str(tmp_path)


@pytest.fixture
def normalize_messages() -> Any:
    """Return a callable that strips non-deterministic fields from messages."""

    def _normalize(messages: list[Any]) -> list[dict[str, Any]]:
        normalized: list[dict[str, Any]] = []
        for msg in messages:
            role    = str(getattr(msg, "role", "")).lower()
            content = getattr(msg, "content", "")

            if not isinstance(content, str):
                content = json.dumps(content)

            content = _UUID_PATTERN.sub("<UUID>", content)
            content = _ISO_TS_PATTERN.sub("<TS>", content)
            content = _PYTEST_TMP_PATTERN.sub("<CWD>", content)

            tool_uses = getattr(msg, "tool_uses", [])
            normed_tool_uses: list[dict[str, Any]] = []
            for tu in tool_uses:
                normed_tu = dict(tu)
                if "id" in normed_tu:
                    normed_tu["id"] = "<TOOL_ID>"
                normed_tool_uses.append(normed_tu)

            entry: dict[str, Any] = {"role": role, "content": content}
            if normed_tool_uses:
                entry["tool_uses"] = normed_tool_uses

            tool_use_id = getattr(msg, "tool_use_id", None)
            if tool_use_id:
                entry["tool_use_id"] = "<TOOL_ID>"

            is_error = getattr(msg, "is_error", False)
            if is_error:
                entry["is_error"] = True

            normalized.append(entry)
        return normalized

    return _normalize


@pytest.fixture
def build_loop(session_dir: str) -> Any:
    """Factory fixture: build_loop(fixture_name, extra_session={}) -> (loop, mock)."""

    def _factory(
        fixture_name: str,
        extra_session: dict[str, Any] | None = None,
    ) -> tuple[AgentLoop, ProviderMock]:
        return _build_agent_loop(session_dir, fixture_name, extra_session)

    return _factory
