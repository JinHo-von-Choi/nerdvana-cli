"""``nerdvana run --json-schema``, ``--input-format stream-json`` and ``--diagnostics-dir`` through the real command."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from nerdvana_cli.cli.run_extras import stream_input_messages, structured_answer
from nerdvana_cli.core.loop.agent_loop import AgentLoop
from nerdvana_cli.main import app
from nerdvana_cli.providers.base import ProviderEvent

runner = CliRunner()


class _Answers:
    """A provider that answers each request with the next scripted text."""

    def __init__(self, texts: list[str]) -> None:
        self.texts   = list(texts)
        self.prompts: list[str] = []

    async def stream(self, system_prompt: str, messages: Any, tools: Any) -> AsyncIterator[ProviderEvent]:
        last = messages[-1]["content"]
        self.prompts.append(last if isinstance(last, str) else json.dumps(last))
        yield ProviderEvent(type="content_delta", content=self.texts.pop(0))
        yield ProviderEvent(type="usage", usage={"input_tokens": 10, "output_tokens": 2})
        yield ProviderEvent(type="done", stop_reason="end_turn")


@pytest.fixture()
def env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("NERDVANA_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("NERDVANA_NO_UPDATE_CHECK", "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.delenv("NERDVANA_CONFIG", raising=False)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(AgentLoop, "build_system_prompt", lambda self: "system")
    return tmp_path


def _provider(monkeypatch: pytest.MonkeyPatch, texts: list[str]) -> _Answers:
    provider = _Answers(texts)
    monkeypatch.setattr(AgentLoop, "create_provider_from_settings", lambda self: provider)
    return provider


SCHEMA = json.dumps({"type": "object", "properties": {"count": {"type": "integer"}}, "required": ["count"]})


def test_a_fitting_answer_becomes_structured_output(env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _provider(monkeypatch, ['```json\n{"count": 3}\n```'])
    result = runner.invoke(app, ["run", "count", "--output-format", "json", "--json-schema", SCHEMA])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["structured_output"] == {"count": 3}


def test_a_misfit_is_sent_back_and_the_corrected_answer_kept(env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    provider = _provider(monkeypatch, ["three", '{"count": 3}'])
    result = runner.invoke(app, ["run", "count", "--output-format", "json", "--json-schema", SCHEMA])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["structured_output"] == {"count": 3}
    assert "JSON schema" in provider.prompts[1]


def test_an_answer_that_never_fits_fails_with_error_schema(env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _provider(monkeypatch, ["no", "still no", "never"])
    result = runner.invoke(app, ["run", "count", "--output-format", "json", "--json-schema", SCHEMA])
    payload = json.loads(result.stdout)
    assert result.exit_code == 1
    assert payload["subtype"] == "error_schema" and "structured_output" not in payload


def test_an_invalid_schema_is_a_configuration_error(env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _provider(monkeypatch, ["x"])
    result = runner.invoke(app, ["run", "count", "--output-format", "json", "--json-schema", '{"type": 5}'])
    assert result.exit_code == 2


def test_stream_json_input_runs_one_turn_per_line(env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    provider = _provider(monkeypatch, ["one", "two"])
    stdin = json.dumps({"type": "user", "message": {"content": "first"}}) + "\n\n" + json.dumps({"text": "second"}) + "\n"
    result = runner.invoke(app, ["run", "-", "--input-format", "stream-json", "--output-format", "json"], input=stdin)
    assert result.exit_code == 0, result.output
    assert provider.prompts == ["first", "second"]
    assert json.loads(result.stdout)["result"] == "two"


def test_the_diagnostics_folder_keeps_events_settings_and_result(env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _provider(monkeypatch, ["done"])
    folder = env / "diag"
    result = runner.invoke(app, ["run", "go", "--diagnostics-dir", str(folder)])
    assert result.exit_code == 0, result.output
    events = [json.loads(line) for line in (folder / "events.jsonl").read_text().splitlines()]
    assert {"text", "request"} <= {e["type"] for e in events}
    assert json.loads((folder / "result.json").read_text())["result"] == "done"
    assert "test-key" not in (folder / "settings.yml").read_text()


def test_a_non_empty_diagnostics_folder_is_refused(env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _provider(monkeypatch, ["done"])
    folder = env / "diag"
    folder.mkdir()
    (folder / "old.txt").write_text("x")
    assert runner.invoke(app, ["run", "go", "--diagnostics-dir", str(folder)]).exit_code == 2


def test_stream_input_rejects_a_line_that_is_not_a_message() -> None:
    with pytest.raises(ValueError, match="line 1"):
        list(stream_input_messages(['{"kind": "other"}']))


def test_structured_answer_names_the_failing_field() -> None:
    value, problem = structured_answer('{"count": "3"}', json.loads(SCHEMA))
    assert value is None and problem.startswith("count:")
