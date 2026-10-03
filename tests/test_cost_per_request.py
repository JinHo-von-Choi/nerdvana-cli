"""nerdvana cost reads the usage each request reported, cache columns included.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from nerdvana_cli.commands.cost_command import build_cost_report
from nerdvana_cli.core.telemetry.analytics import AnalyticsWriter


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
    from nerdvana_cli.core.telemetry.analytics import AnalyticsReader

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


# ---------------------------------------------------------------------------
# Attribution: which agent, category and tool the money went to
# ---------------------------------------------------------------------------


def _attributed(tmp_path: Path) -> Path:
    from nerdvana_cli.core.telemetry.analytics import CallOrigin

    writer, db = _writer(tmp_path, "attr")
    usage = {"input_tokens": 1_000_000, "output_tokens": 0}
    writer.record_api_call("anthropic", "claude-sonnet-5-5", usage, CallOrigin(turn=1))
    writer.record_api_call("anthropic", "claude-sonnet-5-5", usage, CallOrigin(turn=2, last_tool="Grep"))
    writer.record_api_call("anthropic", "claude-haiku-4-5-20251001", usage, CallOrigin(agent_id="agent_1", agent_type="Explore", category="quick", parent_session_id="attr", turn=1, last_tool="Grep"))
    return db


def test_the_cost_can_be_grouped_by_agent_category_and_the_tool_before_the_request(tmp_path: Path) -> None:
    db = _attributed(tmp_path)
    by_agent    = {r["provider"]: r for r in build_cost_report(since="all", by="agent", db_path=db)["rows"]}
    by_category = {r["provider"]: r for r in build_cost_report(since="all", by="category", db_path=db)["rows"]}
    by_tool     = {r["provider"]: r for r in build_cost_report(since="all", by="tool", db_path=db)["rows"]}
    assert set(by_agent) == {"main", "Explore"}
    assert by_agent["main"]["input_tokens"] == 2_000_000
    assert by_category["quick"]["input_tokens"] == 1_000_000 and by_category["(none)"]["input_tokens"] == 2_000_000
    assert by_tool["Grep"]["input_tokens"] == 2_000_000 and by_tool["(first request)"]["input_tokens"] == 1_000_000
    assert all(r["status"] == "ok" for r in by_agent.values())


def test_the_groups_add_up_to_the_same_total_as_the_model_view(tmp_path: Path) -> None:
    db    = _attributed(tmp_path)
    total = build_cost_report(since="all", by="model", db_path=db)["total_cost_usd"]
    for by in ("agent", "category", "tool"):
        assert build_cost_report(since="all", by=by, db_path=db)["total_cost_usd"] == pytest.approx(total)


def test_sessions_recorded_before_attribution_count_as_main_and_legacy_rows_are_left_out(tmp_path: Path) -> None:
    db = tmp_path / "analytics.sqlite"
    conn = sqlite3.connect(db)
    conn.executescript("""
        CREATE TABLE api_calls (id INTEGER PRIMARY KEY, session_id TEXT, ts TEXT NOT NULL, provider TEXT, model TEXT,
            input_tokens INTEGER, output_tokens INTEGER, cache_read_tokens INTEGER, cache_write_tokens INTEGER, cost_usd REAL);
        INSERT INTO api_calls (session_id, ts, provider, model, input_tokens, output_tokens, cache_read_tokens, cache_write_tokens, cost_usd)
            VALUES ('old', '2999-01-01T00:00:00+00:00', 'anthropic', 'm', 10, 1, 0, 0, 0.5);
    """)
    conn.commit()
    conn.close()
    AnalyticsWriter(db_path=db)  # opens the old database: the new columns are added in place
    (row,) = build_cost_report(since="all", by="agent", db_path=db)["rows"]
    assert (row["provider"], row["input_tokens"]) == ("main", 10)


def test_an_unknown_grouping_is_refused() -> None:
    import typer

    from nerdvana_cli.commands.cost_command import cost_command

    with pytest.raises(typer.Exit):
        cost_command("7d", False, "colour")


def test_stream_json_carries_one_request_event_per_provider_request() -> None:
    import json

    from nerdvana_cli.cli.run_output import RunReporter

    lines: list[str] = []
    RunReporter("stream-json", lines.append).request(
        {"provider": "anthropic", "model": "m", "agent_type": "Explore", "turn": 2, "last_tool": "Grep", "input_tokens": 5, "output_tokens": 1, "cost_usd": 0.01}
    )
    event = json.loads(lines[0])
    assert event["type"] == "request" and event["agent_type"] == "Explore" and event["cache_read_tokens"] == 0
    quiet: list[str] = []
    RunReporter("json", quiet.append).request({"input_tokens": 1})
    assert quiet == []
