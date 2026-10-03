"""Search of stored transcripts: the FTS5 index, the substring scan, filters and masking.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from nerdvana_cli.cli import history_search
from nerdvana_cli.cli.history_search import HistoryIndex, fts_match, make_snippet, search_history, stamp_of
from nerdvana_cli.core.safety.secrets import SecretMasker
from nerdvana_cli.core.session import SessionStorage

NOW = datetime.now(UTC)


def write_session(directory: Path, session_id: str, entries: list[dict[str, Any]], cwd: str | None = "/work/app") -> Path:
    """A transcript with a session_start entry (unless *cwd* is None) and the given entries."""
    lines = [] if cwd is None else [{"ts": NOW.isoformat(), "type": "system", "subtype": "session_start", "cwd": cwd}]
    path  = directory / f"{session_id}.jsonl"
    path.write_text("".join(json.dumps(e) + "\n" for e in [*lines, *entries]), encoding="utf-8")
    return path


def entry(kind: str, content: str, age: timedelta = timedelta(0), **extra: Any) -> dict[str, Any]:
    return {"ts": (NOW - age).isoformat(), "type": kind, "content": content, **extra}


@pytest.fixture
def sessions(tmp_path: Path) -> Path:
    directory = tmp_path / "sessions"
    directory.mkdir()
    write_session(directory, "aaa", [
        entry("user", "How does the retry logic in the uploader work?", timedelta(days=10)),
        entry("assistant", "The uploader retries three times with backoff.", timedelta(days=10)),
    ])
    write_session(directory, "bbb", [
        entry("user", "Rename the Parser class", timedelta(days=1)),
        entry("tool_result", "parser.py: class Parser:", timedelta(hours=25), tool_use_id="t", tool_name="Grep"),
        entry("assistant", "Renamed Parser to Reader in three files.", timedelta(hours=23)),
    ], cwd="/work/other")
    return directory


@pytest.fixture(params=["index", "scan"])
def search(request: pytest.FixtureRequest, tmp_path: Path, sessions: Path) -> Any:
    """The same search through the FTS5 index and through the substring scan."""
    index = tmp_path / "idx" / "history.sqlite" if request.param == "index" else None

    def run(query: str, **kwargs: Any) -> list[history_search.Hit]:
        return search_history(query, sessions, index_path=index, **kwargs)

    return run


class TestSearch:
    def test_finds_a_message_and_reports_session_role_and_snippet(self, search: Any) -> None:
        [hit] = search("retry uploader", limit=5)
        assert (hit.session_id, hit.role) == ("aaa", "user")
        assert "retry logic" in hit.snippet

    def test_every_word_must_be_present(self, search: Any) -> None:
        assert search("uploader rename") == []

    def test_the_newest_message_comes_first(self, search: Any) -> None:
        hits = search("parser")
        assert [h.role for h in hits] == ["assistant", "user", "tool"]

    def test_the_tool_role_is_reported_for_tool_results(self, search: Any) -> None:
        assert {h.role for h in search("class")} >= {"tool"}

    def test_the_search_is_case_insensitive(self, search: Any) -> None:
        assert search("UPLOADER")

    def test_limit_keeps_the_newest(self, search: Any) -> None:
        hits = search("parser", limit=1)
        assert len(hits) == 1 and hits[0].role == "assistant"

    def test_since_drops_older_messages(self, search: Any) -> None:
        assert {h.session_id for h in search("the", since=NOW - timedelta(days=2))} == {"bbb"}
        assert {h.session_id for h in search("the")} == {"aaa", "bbb"}

    def test_cwd_keeps_sessions_started_there_or_below(self, search: Any) -> None:
        assert {h.session_id for h in search("the", cwd="/work/app")} == {"aaa"}
        assert {h.session_id for h in search("the", cwd="/work")} == {"aaa", "bbb"}
        assert search("the", cwd="/work/ap") == []

    def test_a_session_without_a_recorded_directory_is_left_out_when_cwd_is_given(self, sessions: Path, tmp_path: Path) -> None:
        write_session(sessions, "old", [entry("user", "legacy uploader question")], cwd=None)
        assert search_history("legacy", sessions, cwd="/work/app") == []
        assert [h.session_id for h in search_history("legacy", sessions)] == ["old"]

    def test_a_query_without_words_finds_nothing(self, search: Any) -> None:
        assert search("  -- ") == []


class TestMatching:
    def test_the_index_matches_word_prefixes(self, tmp_path: Path, sessions: Path) -> None:
        assert search_history("upload", sessions, index_path=tmp_path / "i.sqlite")

    def test_the_scan_matches_substrings(self, sessions: Path) -> None:
        assert search_history("ploade", sessions)

    def test_a_term_with_quotes_and_punctuation_is_safe_for_fts(self, tmp_path: Path, sessions: Path) -> None:
        assert search_history('say "hi" (now) OR', sessions, index_path=tmp_path / "i.sqlite") == []
        assert fts_match('a "b" c.d') == '"a"* AND "b"* AND "c d"*'

    def test_non_latin_text_is_found_by_its_prefix(self, tmp_path: Path) -> None:
        directory = tmp_path / "s"
        directory.mkdir()
        write_session(directory, "ko", [entry("user", "세션 기록을 검색하는 방법")])
        assert search_history("검색", directory, index_path=tmp_path / "i.sqlite")
        assert search_history("검색", directory)


class TestSnippet:
    def test_the_snippet_surrounds_the_match_and_is_one_line(self) -> None:
        text    = "start\n" + "word " * 60 + "NEEDLE here\n" + "tail " * 60
        snippet = make_snippet(text, "needle")
        assert "NEEDLE here" in snippet and "\n" not in snippet
        assert snippet.startswith("...") and snippet.endswith("...")
        assert len(snippet) < 200

    def test_a_short_text_is_returned_whole(self) -> None:
        assert make_snippet("tiny needle text", "needle") == "tiny needle text"


class TestSecrets:
    SECRET = "sk-ant-abcdefghijklmnopqrstuvwxyz0123456789"

    def test_secrets_in_a_transcript_are_masked_in_what_is_shown(self, search: Any, sessions: Path) -> None:
        write_session(sessions, "ccc", [entry("user", f"my key is {self.SECRET} please keep it")])
        [hit] = search("please keep")
        assert self.SECRET not in hit.snippet and "[REDACTED]" in hit.snippet

    def test_the_index_file_does_not_hold_the_secret(self, tmp_path: Path, sessions: Path) -> None:
        write_session(sessions, "ccc", [entry("user", f"my key is {self.SECRET} please keep it")])
        index = tmp_path / "i.sqlite"
        search_history("please", sessions, index_path=index)
        assert self.SECRET.encode() not in index.read_bytes()

    def test_environment_secret_values_are_masked(self, monkeypatch: pytest.MonkeyPatch, sessions: Path) -> None:
        monkeypatch.setenv("DEPLOY_TOKEN", "hunter2-hunter2-hunter2")
        write_session(sessions, "ddd", [entry("user", "deploy with hunter2-hunter2-hunter2 now")])
        [hit] = search_history("deploy", sessions)
        assert "hunter2" not in hit.snippet


class TestIndex:
    def rows(self, index: Path) -> int:
        with sqlite3.connect(index) as db:
            return int(db.execute("SELECT count(*) FROM docs").fetchone()[0])

    def sync(self, sessions: Path, index: Path) -> None:
        handle = HistoryIndex(index)
        try:
            handle.sync(sessions, SecretMasker())
        finally:
            handle.close()

    def test_only_the_appended_part_of_a_transcript_is_read_again(self, tmp_path: Path, sessions: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        index = tmp_path / "i.sqlite"
        self.sync(sessions, index)
        first = self.rows(index)
        reads: list[int] = []
        original = history_search._read_new_lines
        monkeypatch.setattr(history_search, "_read_new_lines", lambda path, offset: (reads.append(offset), original(path, offset))[1])
        path        = sessions / "aaa.jsonl"
        size_before = path.stat().st_size
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry("user", "a brand new question")) + "\n")
        self.sync(sessions, index)
        assert self.rows(index) == first + 1
        assert reads == [size_before]
        assert [h.snippet for h in search_history("brand", sessions, index_path=index)] == ["a brand new question"]

    def test_an_unchanged_store_adds_nothing(self, tmp_path: Path, sessions: Path) -> None:
        index = tmp_path / "i.sqlite"
        self.sync(sessions, index)
        first = self.rows(index)
        self.sync(sessions, index)
        assert self.rows(index) == first

    def test_a_half_written_last_line_is_left_for_the_next_search(self, tmp_path: Path, sessions: Path) -> None:
        index = tmp_path / "i.sqlite"
        path  = sessions / "aaa.jsonl"
        with path.open("a", encoding="utf-8") as handle:
            handle.write('{"ts": "' + NOW.isoformat() + '", "type": "user", "content": "unfinis')
        assert search_history("unfinis", sessions, index_path=index) == []
        with path.open("a", encoding="utf-8") as handle:
            handle.write('hed thought"}\n')
        assert len(search_history("unfinished", sessions, index_path=index)) == 1

    def test_a_deleted_transcript_leaves_the_index(self, tmp_path: Path, sessions: Path) -> None:
        index = tmp_path / "i.sqlite"
        search_history("uploader", sessions, index_path=index)
        (sessions / "aaa.jsonl").unlink()
        assert search_history("uploader", sessions, index_path=index) == []

    def test_a_transcript_that_shrank_is_read_again_from_the_start(self, tmp_path: Path, sessions: Path) -> None:
        index = tmp_path / "i.sqlite"
        search_history("uploader", sessions, index_path=index)
        write_session(sessions, "aaa", [entry("user", "short")])
        assert search_history("uploader", sessions, index_path=index) == []
        assert len(search_history("short", sessions, index_path=index)) == 1

    def test_a_missing_sessions_directory_is_an_empty_result(self, tmp_path: Path) -> None:
        assert search_history("x", tmp_path / "none", index_path=tmp_path / "i.sqlite") == []
        assert search_history("x", tmp_path / "none") == []


class TestFallback:
    def test_without_fts5_the_scan_answers_and_no_index_is_written(self, tmp_path: Path, sessions: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(history_search, "fts5_available", lambda: False)
        index = tmp_path / "i.sqlite"
        assert [h.session_id for h in search_history("uploader", sessions, index_path=index)] == ["aaa", "aaa"]
        assert not index.exists()

    def test_an_index_that_cannot_be_opened_falls_back_to_the_scan(self, tmp_path: Path, sessions: Path) -> None:
        index = tmp_path / "i.sqlite"
        index.write_bytes(b"this is not a database" * 100)
        assert search_history("uploader", sessions, index_path=index)


def test_stamp_is_sortable_utc_text() -> None:
    assert stamp_of(datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)) == "2026-01-02T03:04:05"


class TestSessionStorageRecordsItsDirectory:
    def test_the_first_record_writes_a_session_start_with_the_working_directory(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        storage = SessionStorage(session_id="s1", storage_dir=str(tmp_path / "store"))
        storage.record_user_message("one")
        storage.record_user_message("two")
        entries = storage.replay()
        assert [e["type"] for e in entries] == ["system", "user", "user"]
        assert entries[0]["subtype"] == "session_start" and entries[0]["cwd"] == str(tmp_path.resolve())

    def test_a_resumed_transcript_gets_no_second_header(self, tmp_path: Path) -> None:
        first = SessionStorage(session_id="s1", storage_dir=str(tmp_path))
        first.record_user_message("one")
        again = SessionStorage(session_id="s1", storage_dir=str(tmp_path))
        again.record_user_message("two")
        assert [e["type"] for e in again.replay()].count("system") == 1

    def test_the_header_does_not_become_a_conversation_message(self, tmp_path: Path) -> None:
        storage = SessionStorage(session_id="s1", storage_dir=str(tmp_path))
        storage.record_user_message("one")
        assert [m.content for m in storage.load_messages()] == ["one"]

    def test_a_search_finds_the_session_by_its_directory(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        work = tmp_path / "work"
        work.mkdir()
        monkeypatch.chdir(work)
        store = tmp_path / "store"
        SessionStorage(session_id="s1", storage_dir=str(store)).record_user_message("needle in a haystack")
        assert [h.session_id for h in search_history("needle", store, cwd=str(work.resolve()))] == ["s1"]
        assert search_history("needle", store, cwd=str((tmp_path / "elsewhere").resolve())) == []
