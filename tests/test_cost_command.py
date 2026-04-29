"""Unit and integration tests for nerdvana_cli.commands.cost_command.

작성자: 최진호
작성일: 2026-04-29
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_db(path: Path) -> None:
    """Create a minimal analytics.sqlite with the tool_calls schema."""
    conn = sqlite3.connect(str(path))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS tool_calls (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id    TEXT,
            tool_name     TEXT NOT NULL,
            start_ts      TEXT NOT NULL,
            duration_ms   INTEGER,
            success       INTEGER NOT NULL DEFAULT 1,
            error_class   TEXT,
            provider      TEXT,
            model         TEXT,
            input_tokens  INTEGER DEFAULT 0,
            output_tokens INTEGER DEFAULT 0,
            cost_usd      REAL    DEFAULT 0.0
        )
    """)
    conn.commit()
    conn.close()


def _insert_row(
    path:          Path,
    provider:      str,
    model:         str,
    input_tokens:  int,
    output_tokens: int,
    cost_usd:      float,
    ts_offset_h:   float = 0.0,
) -> None:
    """Insert one tool_call row into the DB.

    ``ts_offset_h`` is subtracted from 'now', so positive values put the row
    in the past.
    """
    ts = (datetime.now(UTC) - timedelta(hours=ts_offset_h)).isoformat()
    conn = sqlite3.connect(str(path))
    conn.execute(
        """INSERT INTO tool_calls
           (tool_name, start_ts, success, provider, model,
            input_tokens, output_tokens, cost_usd)
           VALUES ('ask', ?, 1, ?, ?, ?, ?, ?)""",
        (ts, provider, model, input_tokens, output_tokens, cost_usd),
    )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# parse_since
# ---------------------------------------------------------------------------

class TestParseSince:
    def test_days(self) -> None:
        from nerdvana_cli.commands.cost_command import parse_since
        cutoff = parse_since("7d")
        assert cutoff is not None
        delta  = datetime.now(UTC) - cutoff
        assert abs(delta.total_seconds() - 7 * 86400) < 5

    def test_hours(self) -> None:
        from nerdvana_cli.commands.cost_command import parse_since
        cutoff = parse_since("24h")
        assert cutoff is not None
        delta  = datetime.now(UTC) - cutoff
        assert abs(delta.total_seconds() - 86400) < 5

    def test_30d(self) -> None:
        from nerdvana_cli.commands.cost_command import parse_since
        cutoff = parse_since("30d")
        assert cutoff is not None
        delta  = datetime.now(UTC) - cutoff
        assert abs(delta.total_seconds() - 30 * 86400) < 5

    def test_all_returns_none(self) -> None:
        from nerdvana_cli.commands.cost_command import parse_since
        assert parse_since("all") is None

    def test_invalid_raises(self) -> None:
        from nerdvana_cli.commands.cost_command import parse_since
        with pytest.raises(ValueError, match="Unrecognised"):
            parse_since("1w")

    def test_invalid_empty_raises(self) -> None:
        from nerdvana_cli.commands.cost_command import parse_since
        with pytest.raises(ValueError):
            parse_since("xyz")


# ---------------------------------------------------------------------------
# load_usage_rows
# ---------------------------------------------------------------------------

class TestLoadUsageRows:
    def test_missing_db_returns_empty(self, tmp_path: Path) -> None:
        from nerdvana_cli.commands.cost_command import load_usage_rows
        result = load_usage_rows(tmp_path / "no.sqlite", cutoff=None, by="model")
        assert result == []

    def test_basic_aggregation_by_model(self, tmp_path: Path) -> None:
        from nerdvana_cli.commands.cost_command import load_usage_rows
        db = tmp_path / "analytics.sqlite"
        _make_db(db)
        _insert_row(db, "anthropic", "claude-sonnet-4-6", 1000, 500, 4.5)
        _insert_row(db, "anthropic", "claude-sonnet-4-6", 2000, 800, 9.0)
        rows = load_usage_rows(db, cutoff=None, by="model")
        assert len(rows) == 1
        r = rows[0]
        assert r["provider"] == "anthropic"
        assert r["model"]    == "claude-sonnet-4-6"
        assert r["input_tokens"]  == 3000
        assert r["output_tokens"] == 1300
        assert abs(r["cost_usd"] - 13.5) < 0.001

    def test_basic_aggregation_by_provider(self, tmp_path: Path) -> None:
        from nerdvana_cli.commands.cost_command import load_usage_rows
        db = tmp_path / "analytics.sqlite"
        _make_db(db)
        _insert_row(db, "anthropic", "claude-sonnet-4-6",  1000, 500, 4.5)
        _insert_row(db, "openai",    "gpt-4o",             500,  200, 2.0)
        rows = load_usage_rows(db, cutoff=None, by="provider")
        providers = {r["provider"] for r in rows}
        assert "anthropic" in providers
        assert "openai"    in providers

    def test_time_filter_excludes_old_rows(self, tmp_path: Path) -> None:
        from nerdvana_cli.commands.cost_command import load_usage_rows, parse_since
        db = tmp_path / "analytics.sqlite"
        _make_db(db)
        # 1 hour ago — within 24h window
        _insert_row(db, "anthropic", "claude-sonnet-4-6", 1000, 500, 4.5, ts_offset_h=1)
        # 30 days ago — outside 7d window
        _insert_row(db, "openai", "gpt-4o", 500, 200, 2.0, ts_offset_h=30 * 24)
        cutoff = parse_since("7d")
        rows   = load_usage_rows(db, cutoff=cutoff, by="model")
        assert len(rows) == 1
        assert rows[0]["provider"] == "anthropic"

    def test_time_filter_all_includes_everything(self, tmp_path: Path) -> None:
        from nerdvana_cli.commands.cost_command import load_usage_rows
        db = tmp_path / "analytics.sqlite"
        _make_db(db)
        _insert_row(db, "anthropic", "claude-sonnet-4-6", 1000, 500, 4.5, ts_offset_h=1000)
        _insert_row(db, "openai",    "gpt-4o",            500,  200, 2.0, ts_offset_h=5000)
        rows = load_usage_rows(db, cutoff=None, by="model")
        assert len(rows) == 2

    def test_multi_provider_by_model(self, tmp_path: Path) -> None:
        from nerdvana_cli.commands.cost_command import load_usage_rows
        db = tmp_path / "analytics.sqlite"
        _make_db(db)
        _insert_row(db, "anthropic", "claude-opus-4-7",   120000, 30000, 4.05)
        _insert_row(db, "openai",    "gpt-4.1",           50000,  12000, 0.245)
        rows = load_usage_rows(db, cutoff=None, by="model")
        providers = {r["provider"] for r in rows}
        assert "anthropic" in providers
        assert "openai"    in providers


# ---------------------------------------------------------------------------
# build_cost_report
# ---------------------------------------------------------------------------

class TestBuildCostReport:
    def test_no_db_returns_empty_report(self, tmp_path: Path, monkeypatch) -> None:
        from nerdvana_cli.commands.cost_command import build_cost_report
        monkeypatch.setenv("NERDVANA_DATA_HOME", str(tmp_path / "nonexistent"))
        report = build_cost_report(since="7d", by="model")
        assert report["rows"] == []
        assert report["total_input"]    == 0
        assert report["total_output"]   == 0
        assert report["total_cost_usd"] == 0.0

    def test_invalid_since_returns_error(self, tmp_path: Path) -> None:
        from nerdvana_cli.commands.cost_command import build_cost_report
        report = build_cost_report(since="bad", by="model",
                                   db_path=tmp_path / "a.sqlite")
        assert "error" in report

    def test_totals_match_row_sum(self, tmp_path: Path) -> None:
        from nerdvana_cli.commands.cost_command import build_cost_report
        db = tmp_path / "analytics.sqlite"
        _make_db(db)
        _insert_row(db, "anthropic", "claude-sonnet-4-6", 1000, 500, 4.5)
        _insert_row(db, "openai",    "gpt-4o",            500,  200, 2.0)
        report = build_cost_report(since="all", by="model", db_path=db)
        row_input_sum  = sum(r["input_tokens"]  for r in report["rows"])
        row_output_sum = sum(r["output_tokens"] for r in report["rows"])
        row_cost_sum   = sum(r["cost_usd"]      for r in report["rows"])
        assert report["total_input"]    == row_input_sum
        assert report["total_output"]   == row_output_sum
        assert abs(report["total_cost_usd"] - row_cost_sum) < 0.0001

    def test_warning_count_for_unknown_model(self, tmp_path: Path) -> None:
        from nerdvana_cli.commands.cost_command import build_cost_report
        db = tmp_path / "analytics.sqlite"
        _make_db(db)
        # "dashscope" / "qwen3-coder-plus" has pricing TBD in pricing.yml
        _insert_row(db, "dashscope", "qwen3-coder-plus", 80000, 20000, 0.0)
        report = build_cost_report(since="all", by="model", db_path=db)
        assert report["warning_count"] >= 1

    def test_known_model_no_warning(self, tmp_path: Path) -> None:
        from nerdvana_cli.commands.cost_command import build_cost_report
        db = tmp_path / "analytics.sqlite"
        _make_db(db)
        _insert_row(db, "anthropic", "claude-sonnet-4-6", 1000, 500, 4.5)
        report = build_cost_report(since="all", by="model", db_path=db)
        assert report["warning_count"] == 0

    def test_by_provider_grouping(self, tmp_path: Path) -> None:
        from nerdvana_cli.commands.cost_command import build_cost_report
        db = tmp_path / "analytics.sqlite"
        _make_db(db)
        _insert_row(db, "anthropic", "claude-sonnet-4-6", 1000, 500, 4.5)
        _insert_row(db, "anthropic", "claude-opus-4-7",   2000, 800, 9.0)
        report = build_cost_report(since="all", by="provider", db_path=db)
        assert len(report["rows"]) == 1
        assert report["rows"][0]["provider"] == "anthropic"
        assert report["rows"][0]["input_tokens"] == 3000

    def test_by_model_grouping(self, tmp_path: Path) -> None:
        from nerdvana_cli.commands.cost_command import build_cost_report
        db = tmp_path / "analytics.sqlite"
        _make_db(db)
        _insert_row(db, "anthropic", "claude-sonnet-4-6", 1000, 500, 4.5)
        _insert_row(db, "anthropic", "claude-opus-4-7",   2000, 800, 9.0)
        report = build_cost_report(since="all", by="model", db_path=db)
        assert len(report["rows"]) == 2
        models = {r["model"] for r in report["rows"]}
        assert "claude-sonnet-4-6" in models
        assert "claude-opus-4-7"   in models

    def test_cutoff_iso_matches_since(self, tmp_path: Path) -> None:
        from nerdvana_cli.commands.cost_command import build_cost_report
        db = tmp_path / "analytics.sqlite"
        _make_db(db)
        report = build_cost_report(since="all", by="model", db_path=db)
        assert report["cutoff_iso"] == "all"

        report2 = build_cost_report(since="7d", by="model", db_path=db)
        assert report2["cutoff_iso"] != "all"


# ---------------------------------------------------------------------------
# JSON output via CliRunner
# ---------------------------------------------------------------------------

class TestCliRunnerJson:
    def test_json_output_valid(self, tmp_path: Path) -> None:
        from typer.testing import CliRunner

        from nerdvana_cli.main import app

        db = tmp_path / "analytics.sqlite"
        _make_db(db)
        _insert_row(db, "anthropic", "claude-sonnet-4-6", 1000, 500, 4.5)

        runner = CliRunner()
        result = runner.invoke(
            app,
            ["cost", "--json", "--since", "all"],
            env={"NERDVANA_DATA_HOME": str(tmp_path)},
            catch_exceptions=False,
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert "rows"           in data
        assert "total_cost_usd" in data
        assert isinstance(data["rows"], list)

    def test_json_empty_data(self, tmp_path: Path) -> None:
        from typer.testing import CliRunner

        from nerdvana_cli.main import app

        runner = CliRunner()
        result = runner.invoke(
            app,
            ["cost", "--json", "--since", "all"],
            env={"NERDVANA_DATA_HOME": str(tmp_path / "nonexistent")},
            catch_exceptions=False,
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["rows"] == []
        assert data["total_cost_usd"] == 0.0

    def test_json_contains_total_cost_usd(self, tmp_path: Path) -> None:
        from typer.testing import CliRunner

        from nerdvana_cli.main import app

        db = tmp_path / "analytics.sqlite"
        _make_db(db)
        _insert_row(db, "anthropic", "claude-opus-4-7",  120000, 30000, 4.05)
        _insert_row(db, "openai",    "gpt-4.1",           50000, 12000, 0.245)

        runner = CliRunner()
        result = runner.invoke(
            app,
            ["cost", "--json", "--since", "all"],
            env={"NERDVANA_DATA_HOME": str(tmp_path)},
            catch_exceptions=False,
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert abs(data["total_cost_usd"] - 4.295) < 0.001

    def test_invalid_by_exits_nonzero(self, tmp_path: Path) -> None:
        from typer.testing import CliRunner

        from nerdvana_cli.main import app

        runner = CliRunner()
        result = runner.invoke(
            app,
            ["cost", "--by", "invalid_key"],
            env={"NERDVANA_DATA_HOME": str(tmp_path)},
            catch_exceptions=False,
        )
        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# Table output (smoke test via CliRunner)
# ---------------------------------------------------------------------------

class TestCliRunnerTable:
    def test_table_output_no_data(self, tmp_path: Path) -> None:
        from typer.testing import CliRunner

        from nerdvana_cli.main import app

        runner = CliRunner()
        result = runner.invoke(
            app,
            ["cost", "--since", "all"],
            env={"NERDVANA_DATA_HOME": str(tmp_path / "nonexistent")},
            catch_exceptions=False,
        )
        assert result.exit_code == 0
        assert "no usage data" in result.output

    def test_table_output_with_data(self, tmp_path: Path) -> None:
        from typer.testing import CliRunner

        from nerdvana_cli.main import app

        db = tmp_path / "analytics.sqlite"
        _make_db(db)
        _insert_row(db, "anthropic", "claude-sonnet-4-6", 1000, 500, 4.5)

        runner = CliRunner()
        result = runner.invoke(
            app,
            ["cost", "--since", "all"],
            env={"NERDVANA_DATA_HOME": str(tmp_path)},
            catch_exceptions=False,
        )
        assert result.exit_code == 0
        assert "anthropic" in result.output
        assert "TOTAL"     in result.output
