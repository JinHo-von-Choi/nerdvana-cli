"""Search of stored session transcripts.

Author: 최진호
Date:   2026-10-03

Transcripts are append-only JSONL files (``core/state/session.py``). The search reads the messages of user,
assistant and tool results from them. With SQLite FTS5 available it keeps a small index file next to
the transcripts that is brought up to date on every search: only the bytes appended since the last
search are read, and a transcript that shrank or vanished is dropped and read again. Without FTS5, or
when the index cannot be used, every transcript is scanned for a substring instead. The index matches
whole words and word prefixes, the scan matches substrings; both need every word of the query to be
in the message and list the newest message first.

Text is passed through ``SecretMasker`` before it is indexed or shown, because user prompts and file
contents in a transcript are not masked when they are written.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
from collections.abc import Iterator
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from nerdvana_cli.core.safety.secrets import SecretMasker

logger = logging.getLogger(__name__)

INDEX_FILENAME = "history-index.sqlite"
SNIPPET_BEFORE = 50
SNIPPET_AFTER  = 90

# Transcript entry type to the role a hit is reported with.
_ROLES  = {"user": "user", "assistant": "assistant", "tool_result": "tool"}
_WORDS  = re.compile(r"\w+")
_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS sessions(session TEXT PRIMARY KEY, offset INTEGER NOT NULL, cwd TEXT NOT NULL)",
    "CREATE VIRTUAL TABLE IF NOT EXISTS docs USING fts5(text, session UNINDEXED, stamp UNINDEXED, role UNINDEXED, cwd UNINDEXED)",
)


@dataclass(frozen=True)
class Hit:
    """One message that matched: where it was said, by whom, and the text around the match."""

    session_id: str
    stamp:      str
    role:       str
    cwd:        str
    snippet:    str


@dataclass(frozen=True)
class _Document:
    stamp: str
    role:  str
    text:  str


def fts5_available() -> bool:
    """Whether the SQLite library of this Python was built with FTS5."""
    try:
        with closing(sqlite3.connect(":memory:")) as db:
            db.execute("CREATE VIRTUAL TABLE probe USING fts5(text)")
    except sqlite3.Error:
        return False
    return True


def stamp_of(moment: datetime) -> str:
    """The sortable UTC text form (to the second) the search compares timestamps in."""
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S")


def _entry_stamp(raw: Any) -> str:
    try:
        return stamp_of(datetime.fromisoformat(str(raw)))
    except ValueError:
        return ""


def _query_terms(query: str) -> list[str]:
    """The whitespace separated terms of *query* that hold at least one word character."""
    return [term for term in query.split() if _WORDS.search(term)]


def fts_match(query: str) -> str:
    """The FTS5 expression for *query*: each term a phrase of its words, the last word a prefix, all terms required."""
    phrases = ['"' + " ".join(_WORDS.findall(term)).replace('"', '""') + '"*' for term in _query_terms(query)]
    return " AND ".join(phrases)


def make_snippet(text: str, query: str) -> str:
    """The text around the first place a term of *query* occurs, on one line."""
    lowered = text.lower()
    found   = [lowered.find(term.lower()) for term in _query_terms(query)]
    first   = min((i for i in found if i >= 0), default=0)
    start   = max(0, first - SNIPPET_BEFORE)
    end     = min(len(text), first + SNIPPET_AFTER)
    body    = " ".join(text[start:end].split())
    return f"{'...' if start else ''}{body}{'...' if end < len(text) else ''}"


def _read_new_lines(path: Path, offset: int) -> tuple[list[dict[str, Any]], int]:
    """The JSON entries appended to *path* after byte *offset* and the offset after the last complete line."""
    with path.open("rb") as handle:
        handle.seek(offset)
        data = handle.read()
    complete = data[: data.rfind(b"\n") + 1]
    entries: list[dict[str, Any]] = []
    for line in complete.splitlines():
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if isinstance(entry, dict):
            entries.append(entry)
    return entries, offset + len(complete)


def _documents(entries: list[dict[str, Any]], masker: SecretMasker) -> Iterator[_Document]:
    for entry in entries:
        role = _ROLES.get(str(entry.get("type", "")))
        text = entry.get("content")
        if role and isinstance(text, str) and text.strip():
            yield _Document(_entry_stamp(entry.get("ts")), role, masker.mask(text).text)


def _session_cwd(entries: list[dict[str, Any]], known: str) -> str:
    """The directory the session was started in, from its ``session_start`` entry; *known* when there is none."""
    for entry in entries:
        if entry.get("type") == "system" and entry.get("subtype") == "session_start":
            return str(entry.get("cwd", ""))
    return known


def cwd_matches(session_cwd: str, wanted: str | None) -> bool:
    """Whether a session started in *session_cwd* belongs to the directory *wanted* (itself or below it)."""
    if wanted is None:
        return True
    return bool(session_cwd) and (session_cwd == wanted or session_cwd.startswith(wanted.rstrip("/") + "/"))


class HistoryIndex:
    """The FTS5 index of a sessions directory, kept in one SQLite file."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path, timeout=10)
        for statement in _SCHEMA:
            self._db.execute(statement)
        self._db.commit()

    def close(self) -> None:
        self._db.close()

    def sync(self, sessions_dir: Path, masker: SecretMasker) -> None:
        """Bring the index up to date with the transcripts in *sessions_dir*."""
        files = {p.stem: p for p in sessions_dir.glob("*.jsonl")} if sessions_dir.is_dir() else {}
        known = {row[0] for row in self._db.execute("SELECT session FROM sessions")}
        for gone in known - files.keys():
            self._forget(gone)
        for session, path in files.items():
            self._sync_file(session, path, masker)
        self._db.commit()

    def _forget(self, session: str) -> None:
        self._db.execute("DELETE FROM docs WHERE session = ?", (session,))
        self._db.execute("DELETE FROM sessions WHERE session = ?", (session,))

    def _sync_file(self, session: str, path: Path, masker: SecretMasker) -> None:
        row    = self._db.execute("SELECT offset, cwd FROM sessions WHERE session = ?", (session,)).fetchone()
        offset, cwd = row if row else (0, "")
        if path.stat().st_size < offset:
            self._forget(session)
            offset, cwd = 0, ""
        if path.stat().st_size == offset and row:
            return
        entries, end = _read_new_lines(path, offset)
        cwd          = _session_cwd(entries, cwd)
        self._db.executemany(
            "INSERT INTO docs(text, session, stamp, role, cwd) VALUES (?, ?, ?, ?, ?)",
            [(d.text, session, d.stamp, d.role, cwd) for d in _documents(entries, masker)],
        )
        self._db.execute("INSERT OR REPLACE INTO sessions(session, offset, cwd) VALUES (?, ?, ?)", (session, end, cwd))

    def query(self, query: str, cutoff: str, cwd: str | None, limit: int) -> list[Hit]:
        """The newest messages matching *query* no older than *cutoff* (a ``stamp_of`` text, empty for any age)."""
        rows = self._db.execute(
            "SELECT session, stamp, role, cwd, text FROM docs WHERE docs MATCH ? AND stamp >= ? ORDER BY stamp DESC",
            (fts_match(query), cutoff),
        )
        hits: list[Hit] = []
        for session, stamp, role, session_cwd, text in rows:
            if cwd_matches(session_cwd, cwd):
                hits.append(Hit(session, stamp, role, session_cwd, make_snippet(text, query)))
                if len(hits) == limit:
                    break
        return hits


def scan_transcripts(sessions_dir: Path, query: str, cutoff: str, cwd: str | None, limit: int, masker: SecretMasker) -> list[Hit]:
    """The newest messages containing every term of *query*, found by reading every transcript."""
    terms = [t.lower() for t in _query_terms(query)]
    found: list[tuple[str, str, Hit]] = []
    for path in sorted(sessions_dir.glob("*.jsonl")) if sessions_dir.is_dir() else []:
        entries, _ = _read_new_lines(path, 0)
        session_cwd = _session_cwd(entries, "")
        if not cwd_matches(session_cwd, cwd):
            continue
        for doc in _documents(entries, masker):
            if doc.stamp >= cutoff and all(t in doc.text.lower() for t in terms):
                found.append((doc.stamp, path.stem, Hit(path.stem, doc.stamp, doc.role, session_cwd, make_snippet(doc.text, query))))
    found.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return [hit for _, _, hit in found[:limit]]


def search_history(
    query:        str,
    sessions_dir: Path,
    *,
    since:        datetime | None = None,
    cwd:          str | None      = None,
    limit:        int             = 20,
    index_path:   Path | None     = None,
) -> list[Hit]:
    """The newest *limit* messages of the transcripts in *sessions_dir* that contain every term of *query*.

    *since* drops older messages, *cwd* keeps the sessions started in that directory or below it. The
    index at *index_path* is used when given and FTS5 is available; otherwise the transcripts are scanned.
    """
    if not _query_terms(query):
        return []
    masker = SecretMasker.from_environment()
    cutoff = stamp_of(since) if since else ""
    if index_path is not None and fts5_available():
        try:
            index = HistoryIndex(index_path)
            try:
                index.sync(sessions_dir, masker)
                return index.query(query, cutoff, cwd, limit)
            finally:
                index.close()
        except sqlite3.Error as exc:
            logger.warning("history index unusable (%s); scanning the transcripts instead", exc)
    return scan_transcripts(sessions_dir, query, cutoff, cwd, limit, masker)
