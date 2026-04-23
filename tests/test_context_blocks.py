"""Tests for context block splitting and summarization."""
from __future__ import annotations

import pytest

from nerdvana_cli.core.compact import (
    _extractive_summary_simple,
    compact_with_blocks,
    split_into_blocks,
    summarize_block,
)


class TestBlockSplitting:
    """Conversation block splitting logic."""

    def test_split_by_message_count(self):
        messages = [{"role": "user", "content": f"msg {i}"} for i in range(20)]
        blocks = split_into_blocks(messages, max_block_size=5)
        assert len(blocks) == 4
        assert all(len(b) <= 5 for b in blocks)

    def test_empty_messages_returns_empty(self):
        assert split_into_blocks([]) == []

    def test_single_block_when_few_messages(self):
        messages = [{"role": "user", "content": "msg"} for _ in range(3)]
        blocks = split_into_blocks(messages, max_block_size=10)
        assert len(blocks) == 1


class TestBlockSummarization:
    """Block summarization logic."""

    def test_summarize_block_returns_summary(self):
        block = [
            {"role": "user", "content": "Explain decorators"},
            {"role": "assistant", "content": "Decorators are functions that modify other functions..."},
        ]
        summary = summarize_block(block)
        assert isinstance(summary, str)
        assert len(summary) > 0

    def test_summarize_empty_block_returns_empty(self):
        assert summarize_block([]) == ""


class TestCompactWithBlocks:
    """Block-based context compaction."""

    def test_compact_reduces_message_count(self):
        messages = [{"role": "user", "content": f"msg {i}"} for i in range(20)]
        result = compact_with_blocks(messages, keep_recent=2, max_block_size=5)
        assert len(result) < len(messages)

    def test_compact_preserves_recent_messages(self):
        messages = [{"role": "user", "content": f"msg {i}"} for i in range(20)]
        result = compact_with_blocks(messages, keep_recent=2, max_block_size=5)
        assert result[-1]["content"] == "msg 19"

    def test_compact_empty_returns_empty(self):
        assert compact_with_blocks([], keep_recent=2) == []

    def test_compact_short_returns_original(self):
        messages = [{"role": "user", "content": "msg"} for _ in range(3)]
        result = compact_with_blocks(messages, keep_recent=2, max_block_size=10)
        assert result == messages
