"""Tests for ThinkBlockParser — chunk-safe <think>...</think> extractor."""

from __future__ import annotations

from nerdvana_cli.core.thinking_parser import ThinkBlockParser  # noqa: E402

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _accumulate(chunks: list[str]) -> tuple[str, str]:
    """Feed all chunks and return (total_content, total_thinking)."""
    parser  = ThinkBlockParser()
    content = ""
    thinking = ""
    for chunk in chunks:
        result   = parser.feed(chunk)
        content  += result.content
        thinking += result.thinking
    final     = parser.flush()
    content  += final.content
    thinking += final.thinking
    return content, thinking


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------

def test_single_chunk() -> None:
    content, thinking = _accumulate(["abc<think>reason</think>def"])
    assert content  == "abcdef"
    assert thinking == "reason"


def test_partial_open_tag_across_boundary() -> None:
    content, thinking = _accumulate(["abc<thi", "nk>reason</think>def"])
    assert content  == "abcdef"
    assert thinking == "reason"


def test_partial_close_tag_across_boundary() -> None:
    content, thinking = _accumulate(["<think>reason</thin", "k>def"])
    assert content  == "def"
    assert thinking == "reason"


def test_multiple_think_blocks() -> None:
    content, thinking = _accumulate(["a<think>1</think>b<think>2</think>c"])
    assert content  == "abc"
    assert thinking == "12"


def test_think_only() -> None:
    content, thinking = _accumulate(["<think>only</think>"])
    assert content  == ""
    assert thinking == "only"


def test_unclosed_tag_flush() -> None:
    """<think> with no closing tag: thinking text is emitted during feed;
    a subsequent flush with no remaining buffer produces an empty result."""
    parser  = ThinkBlockParser()
    interim = parser.feed("<think>open")
    final   = parser.flush()
    assert interim.content  == ""
    assert interim.thinking == "open"
    assert final.content    == ""
    assert final.thinking   == ""


def test_no_think_tag_flush() -> None:
    """Plain text with no tags: content is emitted during feed;
    flush with an empty buffer returns empty ParsedChunk."""
    parser  = ThinkBlockParser()
    interim = parser.feed("plain text")
    final   = parser.flush()
    assert interim.content  == "plain text"
    assert interim.thinking == ""
    assert final.content    == ""
    assert final.thinking   == ""


def test_empty_chunk() -> None:
    parser  = ThinkBlockParser()
    result  = parser.feed("")
    assert result.content  == ""
    assert result.thinking == ""


def test_partial_open_only_then_flush() -> None:
    """Stream ends with a partial open-tag prefix; flush emits it as content."""
    parser = ThinkBlockParser()
    parser.feed("<thi")
    final = parser.flush()
    # Still in content mode, so the buffered partial prefix is content.
    assert final.content  == "<thi"
    assert final.thinking == ""


def test_non_matching_less_than() -> None:
    """A '<' that is not part of a tag is passed through as content;
    'c' inside <think>...</think> is classified as thinking."""
    content, thinking = _accumulate(["a<b<think>c</think>"])
    assert content  == "a<b"
    assert thinking == "c"
