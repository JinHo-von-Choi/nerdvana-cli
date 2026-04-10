"""Tests for context usage markers."""

from nerdvana_cli.core.agent_loop import CONTEXT_USAGE_PREFIX


def test_context_usage_prefix_format():
    marker = f"{CONTEXT_USAGE_PREFIX}42"
    assert marker.startswith(CONTEXT_USAGE_PREFIX)
    pct = int(marker[len(CONTEXT_USAGE_PREFIX):])
    assert pct == 42


def test_context_usage_zero():
    marker = f"{CONTEXT_USAGE_PREFIX}0"
    assert int(marker[len(CONTEXT_USAGE_PREFIX):]) == 0


def test_context_usage_100():
    marker = f"{CONTEXT_USAGE_PREFIX}100"
    assert int(marker[len(CONTEXT_USAGE_PREFIX):]) == 100


def test_context_usage_prefix_is_non_printable():
    assert CONTEXT_USAGE_PREFIX.startswith("\x00")


def test_context_usage_not_confused_with_normal_text():
    chunk = "Hello world"
    assert not chunk.startswith(CONTEXT_USAGE_PREFIX)
