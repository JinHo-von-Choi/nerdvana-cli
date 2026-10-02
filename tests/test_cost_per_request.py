"""nerdvana cost reads the usage each request reported, cache columns included.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from nerdvana_cli.commands.cost_command import build_cost_report
from nerdvana_cli.core.analytics import AnalyticsWriter


def _writer(tmp_path: Path, session: str) -> tuple[AnalyticsWriter, Path]:
    db     = tmp_path / "analytics.sqlite"
    writer = AnalyticsWriter(db_path=db)
    writer.start_session(session)
    return writer, db


def test_a_request_is_recorded_with_its_cached_tokens_and_a_cache_aware_cost(tmp_path: Path) -> None:
    writer, db = _writer(tmp_path, "s1")
    writer.record_api_call("anthropic", "claude-sonnet-5-5", {"input_tokens": 100_000, "output_tokens": 1_000, "cache_read_tokens": 90_000})
    report = build_cost_report(since="all", by="model", db_path=db)
    (row,) = report["rows"]
    assert (row["input_tokens"], row["cache_read_tokens"], row["cache_write_tokens"]) == (100_000, 90_000, 0)
    plain = 100_000 * 2.0 / 1_000_000 + 1_000 * 10.0 / 1_000_000
    assert 0 < row["cost_usd"] < plain
    assert report["total_cache_read"] == 90_000


def test_sessions_without_request_rows_still_count_from_their_tool_calls(tmp_path: Path) -> None:
    writer, db = _writer(tmp_path, "new")
    writer.record_api_call("anthropic", "claude-sonnet-5-5", {"input_tokens": 1_000, "output_tokens": 10})
    conn = sqlite3.connect(db)
    for session, tokens in (("new", 7_000), ("old", 500)):
        conn.execute(
            "INSERT INTO tool_calls (session_id, tool_name, start_ts, success, provider, model, input_tokens, output_tokens, cost_usd)"
            " VALUES (?, 'ask', '2999-01-01T00:00:00+00:00', 1, 'anthropic', 'claude-sonnet-5-5', ?, 0, 0.5)",
            (session, tokens),
        )
    conn.commit()
    conn.close()
    (row,) = build_cost_report(since="all", by="model", db_path=db)["rows"]
    assert row["input_tokens"] == 1_000 + 500


def test_an_unpriced_model_is_recorded_at_zero_cost(tmp_path: Path) -> None:
    writer, db = _writer(tmp_path, "s2")
    writer.record_api_call("openai", "some-unlisted-model", {"input_tokens": 10, "output_tokens": 5})
    (row,) = build_cost_report(since="all", by="model", db_path=db)["rows"]
    assert row["cost_usd"] == 0.0
    assert row["status"] != "ok"


def test_the_session_cost_prefers_request_rows_and_falls_back_to_tool_calls(tmp_path: Path) -> None:
    from nerdvana_cli.core.analytics import AnalyticsReader

    writer, db = _writer(tmp_path, "s3")
    writer.record_api_call("anthropic", "claude-sonnet-5-5", {"input_tokens": 1_000_000, "output_tokens": 0})
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO tool_calls (session_id, tool_name, start_ts, success, cost_usd) VALUES ('legacy', 'ask', 'x', 1, 0.25)"
    )
    conn.commit()
    conn.close()
    reader = AnalyticsReader(db)
    assert reader.session_cost("s3") == 2.0
    assert reader.session_cost("legacy") == 0.25
