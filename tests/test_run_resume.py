"""``nerdvana run --resume``: a run continues the recorded conversation of a session.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from nerdvana_cli.core.agent_loop import AgentLoop
from nerdvana_cli.core.state.session import SessionStorage
from nerdvana_cli.main import app
from nerdvana_cli.providers.base import ProviderEvent

runner = CliRunner()


class _Recording:
    def __init__(self) -> None:
        self.payloads: list[list[dict[str, Any]]] = []

    async def stream(self, system_prompt: str, messages: Any, tools: Any) -> AsyncIterator[ProviderEvent]:
        self.payloads.append([dict(m) for m in messages])
        yield ProviderEvent(type="content_delta", content="continued")
        yield ProviderEvent(type="done", stop_reason="end_turn")


@pytest.fixture()
def provider(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> _Recording:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("NERDVANA_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("NERDVANA_NO_UPDATE_CHECK", "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.delenv("NERDVANA_CONFIG", raising=False)
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(AgentLoop, "build_system_prompt", lambda self: "system")
    recording = _Recording()
    monkeypatch.setattr(AgentLoop, "create_provider_from_settings", lambda self: recording)
    return recording


def test_the_run_continues_the_recorded_conversation_in_the_same_session(provider: _Recording) -> None:
    earlier = SessionStorage(session_id="sess-old")
    earlier.record_user_message("survey the repository")
    earlier.record_assistant_message("I found three modules.")
    result = runner.invoke(app, ["run", "carry on", "--resume", "sess-old", "--output-format", "stream-json"])
    assert result.exit_code == 0, result.output
    events = [json.loads(line) for line in result.stdout.splitlines()]
    assert events[0]["session_id"] == "sess-old" and events[-1]["session_id"] == "sess-old"
    sent = [(m["role"], m["content"]) for m in provider.payloads[0]]
    assert sent[0] == ("user", "survey the repository")
    assert sent[1] == ("assistant", "I found three modules.")
    assert sent[-1] == ("user", "carry on")
    assert [e["type"] for e in SessionStorage(session_id="sess-old").replay()].count("user") == 2


@pytest.mark.parametrize("session_id", ["no-such-session", "../escape"])
def test_a_session_without_a_recorded_conversation_is_a_configuration_error(provider: _Recording, session_id: str) -> None:
    result = runner.invoke(app, ["run", "go", "--resume", session_id, "--output-format", "json"])
    assert result.exit_code == 2
    payload = json.loads(result.stdout)
    assert payload["is_error"] is True and "Cannot resume" in payload["error"]
    assert provider.payloads == []
