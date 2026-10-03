"""Headless output: chunk classification, result object, reporter formats.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

import io
import json
from typing import Any

import pytest
from rich.console import Console

from nerdvana_cli.cli.run_output import (
    EXIT_BUDGET,
    EXIT_CONFIG,
    EXIT_FAILURE,
    EXIT_OK,
    FORMATS,
    SCHEMA_VERSION,
    RunReporter,
    RunResult,
    classify_chunk,
)
from nerdvana_cli.core.loop.agent_loop import (
    COMPACT_STATUS_PREFIX,
    CONTEXT_USAGE_PREFIX,
    TOOL_DONE_PREFIX,
    TOOL_STATUS_PREFIX,
)

# ---------------------------------------------------------------------------
# classify_chunk
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("chunk", "expected"),
    [
        (f'{TOOL_STATUS_PREFIX}FileRead {{"path": "a.py"}}', {"type": "tool_start", "name": "FileRead", "summary": '{"path": "a.py"}'}),
        (f"{TOOL_STATUS_PREFIX}Glob", {"type": "tool_start", "name": "Glob", "summary": ""}),
        (f"{TOOL_DONE_PREFIX}Bash [done]", {"type": "tool_done", "name": "Bash", "is_error": False}),
        (f"{TOOL_DONE_PREFIX}Bash [error]", {"type": "tool_done", "name": "Bash", "is_error": True}),
        (f"{CONTEXT_USAGE_PREFIX}42", {"type": "context", "percent": 42}),
        (f"{CONTEXT_USAGE_PREFIX}oops", {"type": "context", "percent": 0}),
        (f"{COMPACT_STATUS_PREFIX}done", {"type": "compaction", "status": "done"}),
    ],
)
def test_protocol_chunks_become_structured_events(chunk: str, expected: dict[str, Any]) -> None:
    assert classify_chunk(chunk) == expected


@pytest.mark.parametrize(
    ("chunk", "text"),
    [
        ("\n[bold red]Provider error: boom[/bold red]", "Provider error: boom"),
        ("\n[dim yellow][Retrying in 1.0s: retryable][/dim yellow]\n", "[Retrying in 1.0s: retryable]"),
        ("\n[bold yellow]Max turns (3) reached.[/bold yellow]", "Max turns (3) reached."),
    ],
)
def test_loop_notices_are_separated_from_model_text(chunk: str, text: str) -> None:
    assert classify_chunk(chunk) == {"type": "notice", "text": text}


@pytest.mark.parametrize("chunk", ["hello", "[1, 2, 3] is a list", "see [docs] for more", "a [red] word in the middle"])
def test_model_text_stays_text(chunk: str) -> None:
    assert classify_chunk(chunk) == {"type": "text", "text": chunk}


# ---------------------------------------------------------------------------
# RunResult
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("stop", "subtype", "is_error", "code"),
    [
        ("completed",      "success",          False, EXIT_OK),
        ("max_turns",      "error_max_turns",  True,  EXIT_BUDGET),
        ("max_cost",       "error_max_cost",   True,  EXIT_BUDGET),
        ("max_tokens",     "error_max_tokens", True,  EXIT_FAILURE),
        ("provider_error", "error_provider",   True,  EXIT_FAILURE),
        ("error",          "error_during_run", True,  EXIT_FAILURE),
        ("config",         "error_config",     True,  EXIT_CONFIG),
        ("something new",  "error_during_run", True,  EXIT_FAILURE),
    ],
)
def test_outcomes_map_to_subtype_and_exit_code(stop: str, subtype: str, is_error: bool, code: int) -> None:
    result = RunResult(stop=stop)
    payload = result.to_dict()
    assert (payload["subtype"], payload["is_error"], result.exit_code) == (subtype, is_error, code)


def test_result_object_has_the_documented_fields() -> None:
    payload = RunResult(
        result="answer", session_id="s1", provider="p", model="m", turns=3, duration_ms=1200,
        cost_usd=0.123456789, usage={"input_tokens": 10, "output_tokens": 5, "cache_read_tokens": 4},
    ).to_dict()
    assert payload == {
        "type": "result", "schema_version": SCHEMA_VERSION, "subtype": "success", "is_error": False,
        "result": "answer", "session_id": "s1", "provider": "p", "model": "m",
        "num_turns": 3, "duration_ms": 1200, "total_cost_usd": 0.123457,
        "usage": {"input_tokens": 10, "output_tokens": 5, "cache_read_tokens": 4, "cache_write_tokens": 0},
        "signals": {},
    }


def test_error_text_appears_only_when_known() -> None:
    assert "error" not in RunResult().to_dict()
    assert RunResult(stop="error", error="boom").to_dict()["error"] == "boom"


# ---------------------------------------------------------------------------
# RunReporter
# ---------------------------------------------------------------------------


def _reporter(fmt: str) -> tuple[RunReporter, io.StringIO, io.StringIO]:
    out     = io.StringIO()
    console = io.StringIO()
    return RunReporter(fmt, out.write, Console(file=console, force_terminal=False, width=200)), out, console


def _lines(out: io.StringIO) -> list[dict[str, Any]]:
    return [json.loads(line) for line in out.getvalue().splitlines()]


def test_unknown_format_is_rejected() -> None:
    with pytest.raises(ValueError):
        RunReporter("xml", lambda _text: None)
    assert set(FORMATS) == {"text", "json", "stream-json"}


def test_text_format_prints_chunks_and_nothing_to_stdout() -> None:
    reporter, out, console = _reporter("text")
    reporter.start("s", "p", "m")
    reporter.chunk("hello ")
    reporter.chunk("world")
    reporter.finish(RunResult())
    assert out.getvalue() == ""
    assert console.getvalue().startswith("hello world")
    assert not reporter.machine_readable


def test_json_format_prints_exactly_one_object_with_the_final_answer() -> None:
    reporter, out, _ = _reporter("json")
    reporter.start("s", "p", "m")
    for chunk in ("Let me look. ", f"{TOOL_STATUS_PREFIX}Grep {{}}", f"{TOOL_DONE_PREFIX}Grep [done]", "The answer is ", "42.", f"{CONTEXT_USAGE_PREFIX}10"):
        reporter.chunk(chunk)
    reporter.finish(RunResult(session_id="s"))
    (payload,) = _lines(out)
    assert payload["type"] == "result"
    assert payload["result"] == "The answer is 42."


def test_stream_json_emits_init_events_and_a_final_result() -> None:
    reporter, out, _ = _reporter("stream-json")
    reporter.start("s1", "anthropic", "m")
    reporter.chunk("hi")
    reporter.chunk(f"{TOOL_STATUS_PREFIX}Bash {{}}")
    reporter.chunk(f"{TOOL_DONE_PREFIX}Bash [error]")
    reporter.chunk("\n[bold red]Error: x[/bold red]")
    reporter.finish(RunResult(stop="provider_error"))
    events = _lines(out)
    assert [e["type"] for e in events] == ["system", "text", "tool_start", "tool_done", "notice", "result"]
    assert events[0]["session_id"] == "s1"
    assert events[-1]["subtype"] == "error_provider"


def test_every_line_of_the_json_formats_is_valid_json_even_for_odd_text() -> None:
    reporter, out, _ = _reporter("stream-json")
    reporter.chunk('quote " backslash \\ newline \n unicode 한글  ')
    reporter.finish(RunResult())
    assert all(isinstance(event, dict) for event in _lines(out))
    assert "한글" in out.getvalue()


def test_failure_in_json_format_prints_a_config_result() -> None:
    reporter, out, _ = _reporter("json")
    outcome = RunResult(stop="config")
    reporter.failure(outcome, "No API key found for anthropic.")
    (payload,) = _lines(out)
    assert payload["subtype"] == "error_config"
    assert payload["error"] == "No API key found for anthropic."


def test_failure_in_text_format_prints_the_message_for_a_person() -> None:
    reporter, out, console = _reporter("text")
    reporter.failure(RunResult(stop="config"), "No API key found for anthropic.")
    assert out.getvalue() == ""
    assert "No API key found for anthropic." in console.getvalue()
