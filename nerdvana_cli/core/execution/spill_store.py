"""Spill storage seam — full large tool outputs on disk instead of the model context.

Task 1.1 of the Harness2/DSH enhancement plan:

- ``SaveTextSpill`` describes what to spill (the ``SaveSpillText`` action payload
  for the DSH store later).
- ``SpillRef`` is what the consumer gets back: a filesystem locator plus a
  retrieval hint that tells the model how to read the content back through
  FileRead/Grep.
- ``LocalSpillStore`` implements the seam on the local filesystem: one directory
  per session digest, files owned exclusively by the user (0600) in a 0700
  directory, names sanitized against path traversal, writes exclusive (O_EXCL)
  so two writers can never interleave the same file.
- ``SpillPolicy`` is the decision layer the tool executor consults for every
  result: short content stays inline, long content is replaced by a head+tail
  preview and the full text goes to the spill store. A real filesystem failure
  (disk full, permissions) must never lose the observable result, so it degrades
  to the inline cut instead of raising.
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import re
import secrets
from dataclasses import dataclass
from pathlib import Path

# Characters of a result kept inline when the caller states no limit of its own.
DEFAULT_INLINE_CHARS = 12_000

# Characters kept from the start of a spilled result; the rest comes from the end.
DEFAULT_HEAD_CHARS = 4_000
DEFAULT_TAIL_CHARS = 2_000

# Characters a cut result leaves for the note that says it was cut (it also names
# the spilled file).
NOTE_RESERVE = 400

# Share of the kept characters taken from the start of the result.
HEAD_SHARE = 0.6

# Characters a suggested_name may keep; everything else (separators, controls,
# Unicode oddities) collapses to an underscore.
_SAFE_NAME_TEXT = re.compile(r"[^A-Za-z0-9_.-]+")

_RETRIEVAL_HINT = (
    "Full result spilled to {locator}. Read it back with FileRead on that path "
    "(offset/limit for parts) or Grep to pull matching lines"
)


@dataclass(frozen=True)
class SaveTextSpill:
    """Everything needed to store one full tool output as text (the SaveSpillText payload)."""

    session_id: str
    tool_name: str
    call_id: str
    suggested_name: str
    content: str


@dataclass(frozen=True)
class SpillRef:
    """Where a spilled result lives and how the model should read it back."""

    locator: str
    bytes_written: int
    retrieval_hint: str


def _sanitize_name(raw: str) -> str:
    """*raw* collapsed to one harmless file-name segment; traversal attempts dissolve."""
    cleaned = _SAFE_NAME_TEXT.sub("_", Path(raw).name).strip("._")
    return cleaned or "spill.txt"


class LocalSpillStore:
    """Local-filesystem implementation of the spill storage seam."""

    def __init__(self, root_dir: Path) -> None:
        self._root = root_dir

    def session_dir(self, session_id: str) -> Path:
        """The directory that holds the spills of *session_id*."""
        digest = hashlib.sha256(session_id.encode("utf-8")).hexdigest()[:16]
        return self._root / f"session-{digest}"

    def save_text(self, request: SaveTextSpill) -> SpillRef:
        """*request.content* written to ``<root>/session-<digest>/<name>``, 0600 file inside a 0700 directory.

        Raises ``OSError`` when the disk refuses the write; the caller decides
        what to do about it.
        """
        session_dir = self.session_dir(request.session_id)
        session_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        for directory in (self._root, session_dir):  # the umask may have stripped the intended bits
            with contextlib.suppress(OSError):
                os.chmod(directory, 0o700)

        stem   = _sanitize_name(request.suggested_name).removesuffix(".txt")
        name   = f"{stem}-{secrets.token_hex(6)}.txt"
        target = session_dir / name
        payload = request.content.encode("utf-8")
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            written = 0
            while written < len(payload):  # os.write may accept a partial buffer
                written += os.write(fd, payload[written:])
        finally:
            os.close(fd)
        return SpillRef(
            locator        = str(target),
            bytes_written  = len(payload),
            retrieval_hint = _RETRIEVAL_HINT.format(locator=target),
        )


class SpillPolicy:
    """Decides what stays inline: short content passes through, long content is spilled.

    The preview is bounded by the inline limit, so applying the policy to a
    result that was already previewed changes nothing.
    """

    def __init__(
        self,
        store:            LocalSpillStore,
        max_inline_chars: int = DEFAULT_INLINE_CHARS,
        head_chars:       int = DEFAULT_HEAD_CHARS,
        tail_chars:       int = DEFAULT_TAIL_CHARS,
    ) -> None:
        self._store            = store
        self._max_inline_chars = max_inline_chars
        self._head_chars       = head_chars
        self._tail_chars       = tail_chars
        kept = head_chars + tail_chars
        self._head_share = head_chars / kept if kept else HEAD_SHARE

    @property
    def max_inline_chars(self) -> int:
        """The most characters kept inline when the caller states no limit of its own."""
        return self._max_inline_chars

    def apply(
        self,
        *,
        session_id: str,
        tool_name: str,
        call_id: str,
        content: str,
        max_inline_chars: int | None = None,
    ) -> str:
        """Content longer than the inline limit is spilled whole and previewed head and tail.

        Content at or below the limit is returned untouched. The preview stays
        within the limit, so applying the policy to it again changes nothing.
        """
        limit = self._max_inline_chars if max_inline_chars is None else max_inline_chars
        if len(content) <= limit:
            return content
        try:
            ref = self._store.save_text(
                SaveTextSpill(
                    session_id     = session_id,
                    tool_name      = tool_name,
                    call_id        = call_id,
                    suggested_name = tool_name,
                    content        = content,
                ),
            )
        except OSError:
            return self._preview(content, limit, "spill store unavailable, so the text was cut inline")
        return self._preview(content, limit, ref.retrieval_hint)

    # -------------------------------------------------------------------------
    # The preview
    # -------------------------------------------------------------------------

    def _preview(self, content: str, limit: int, detail: str) -> str:
        """*content* cut to *limit* characters, head and tail, with a note in between."""
        total  = len(content)
        budget = max(limit - NOTE_RESERVE, limit // 2)
        head   = int(budget * self._head_share)
        tail   = budget - head
        room   = limit - budget
        note   = f"\n\n... [{detail}; {total - budget} of {total} characters omitted] ...\n\n"
        if len(note) > room:  # the detail (the path, the reason) matters more than the counts
            note = f"\n\n... [{detail}] ...\n\n"
        if len(note) > room:
            note = note[:room] if room > 0 else ""
        return content[:head] + note + (content[-tail:] if tail else "")
