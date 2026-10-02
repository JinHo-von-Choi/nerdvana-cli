"""Stream-safe extractor for <think>...</think> blocks."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass
class ParsedChunk:
    content: str = ""
    thinking: str = ""


class ThinkBlockParser:
    """State-machine extractor that splits a stream into content/thinking
    parts.

    Modes: 'content' (default) -> 'thinking' (after <think>) -> back to
    'content' (after </think>). Partial tag tokens that span chunk
    boundaries are buffered and resolved on the next feed.
    """

    OPEN_TAG  = "<think>"
    CLOSE_TAG = "</think>"

    def __init__(self) -> None:
        self._mode: Literal["content", "thinking"] = "content"
        self._buffer: str = ""

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _longest_suffix_prefix(text: str, tag: str) -> int:
        """Return the length of the longest suffix of *text* that is also
        a prefix of *tag*.  Used to detect partial tags that straddle a
        chunk boundary.
        """
        max_len = min(len(text), len(tag) - 1)
        for length in range(max_len, 0, -1):
            if text.endswith(tag[:length]):
                return length
        return 0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def feed(self, chunk: str) -> ParsedChunk:
        """Consume the next chunk; return the (content, thinking) split."""
        result = ParsedChunk()
        self._buffer += chunk

        while True:
            if self._mode == "content":
                tag      = self.OPEN_TAG
                idx      = self._buffer.find(tag)
                if idx != -1:
                    # Emit everything before the tag as content.
                    result.content += self._buffer[:idx]
                    self._buffer    = self._buffer[idx + len(tag):]
                    self._mode      = "thinking"
                    # Continue the loop — there may be a closing tag in the
                    # same buffer.
                    continue
                else:
                    # No complete open tag found.  Check for a partial prefix
                    # at the end so we do not prematurely emit it as content.
                    partial = self._longest_suffix_prefix(self._buffer, tag)
                    if partial:
                        result.content += self._buffer[:-partial]
                        self._buffer    = self._buffer[-partial:]
                    else:
                        result.content += self._buffer
                        self._buffer    = ""
                    break

            else:  # mode == "thinking"
                tag  = self.CLOSE_TAG
                idx  = self._buffer.find(tag)
                if idx != -1:
                    result.thinking += self._buffer[:idx]
                    self._buffer     = self._buffer[idx + len(tag):]
                    self._mode       = "content"
                    continue
                else:
                    partial = self._longest_suffix_prefix(self._buffer, tag)
                    if partial:
                        result.thinking += self._buffer[:-partial]
                        self._buffer     = self._buffer[-partial:]
                    else:
                        result.thinking += self._buffer
                        self._buffer     = ""
                    break

        return result

    def flush(self) -> ParsedChunk:
        """End-of-stream; emit any buffered text as the active mode."""
        result = ParsedChunk()
        if self._buffer:
            if self._mode == "content":
                result.content  = self._buffer
            else:
                result.thinking = self._buffer
            self._buffer = ""
        return result
