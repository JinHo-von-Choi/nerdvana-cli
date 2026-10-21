"""Other coding-agent CLIs as a benchmark substrate: the commands, and the usage read back from their output."""

from __future__ import annotations

import json

import pytest

from nerdvana_cli.core.harness.bench_spec import BenchError, BenchSubstrate
from nerdvana_cli.core.harness.external_cli import EXTERNAL_CLIS, external_command, external_usage


def test_every_cli_gets_the_prompt_and_the_model_flag() -> None:
    for cli in EXTERNAL_CLIS:
        command = external_command(cli, "fix it", "/work", model="m1")
        assert "fix it" in command
        assert command[-1] == "m1"
    assert "/work" in external_command("mcode", "fix it", "/work")


def test_the_substrate_names_an_external_cli() -> None:
    assert BenchSubstrate.parse("external:claude") == BenchSubstrate(name="external", preset="claude")
    with pytest.raises(BenchError):
        BenchSubstrate.parse("external:nope")


def test_claude_usage_counts_cached_prompt_tokens() -> None:
    stdout = json.dumps({"num_turns": 4, "usage": {"input_tokens": 10, "cache_read_input_tokens": 90, "cache_creation_input_tokens": 5, "output_tokens": 7}})
    assert external_usage("claude", stdout) == {"turns": 4, "input_tokens": 105, "output_tokens": 7}


def test_codex_usage_sums_the_turns() -> None:
    lines = [{"type": "turn.completed", "usage": {"input_tokens": 100, "output_tokens": 3}}, {"type": "item.completed"},
             {"type": "turn.completed", "usage": {"input_tokens": 50, "output_tokens": 2}}]
    assert external_usage("codex", "\n".join(json.dumps(line) for line in lines)) == {"input_tokens": 150, "output_tokens": 5}


def test_a_cli_that_reports_nothing_is_unmeasured_not_zero() -> None:
    assert external_usage("copilot", "plain text answer") == {}
    assert external_usage("claude", "not json") == {}
