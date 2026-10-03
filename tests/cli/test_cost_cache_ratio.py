"""nerdvana cost shows how much of the input came from the prompt cache.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from nerdvana_cli.cli.commands.cost_command import build_cost_report, cache_hit_ratio
from nerdvana_cli.core.telemetry.analytics import AnalyticsWriter, CallOrigin
from nerdvana_cli.main import app


def _recorded(tmp_path: Path) -> Path:
    db     = tmp_path / "analytics.sqlite"
    writer = AnalyticsWriter(db_path=db)
    writer.start_session("ratio")
    main   = CallOrigin(turn=1)
    worker = CallOrigin(agent_id="agent_1", agent_type="Explore", category="quick", parent_session_id="ratio", turn=1, last_tool="Grep")
    writer.record_api_call("anthropic", "claude-sonnet-5-5", {"input_tokens": 1_000, "output_tokens": 10, "cache_write_tokens": 800}, main)
    writer.record_api_call("anthropic", "claude-sonnet-5-5", {"input_tokens": 1_000, "output_tokens": 10, "cache_read_tokens": 900}, CallOrigin(turn=2, last_tool="Grep"))
    writer.record_api_call("anthropic", "claude-haiku-4-5-20251001", {"input_tokens": 2_000, "output_tokens": 10}, worker)
    return db


def test_the_ratio_is_cache_read_over_input() -> None:
    assert cache_hit_ratio(900, 1_000) == 0.9
    assert cache_hit_ratio(0, 1_000) == 0.0
    assert cache_hit_ratio(1, 3) == 0.3333


def test_the_ratio_is_undefined_without_input_and_never_above_one() -> None:
    assert cache_hit_ratio(0, 0) is None
    assert cache_hit_ratio(5, 0) is None
    assert cache_hit_ratio(500, 100) == 1.0


@pytest.mark.parametrize("by", ["provider", "model", "agent", "category", "tool"])
def test_every_breakdown_carries_a_ratio_per_row_and_for_the_total(tmp_path: Path, by: str) -> None:
    report = build_cost_report(since="all", by=by, db_path=_recorded(tmp_path))
    assert report["cache_hit_ratio"] == cache_hit_ratio(900, 4_000) == 0.225
    for row in report["rows"]:
        assert row["cache_hit_ratio"] == cache_hit_ratio(row["cache_read_tokens"], row["input_tokens"])


def test_a_group_with_no_cache_reads_has_a_ratio_of_zero(tmp_path: Path) -> None:
    rows = {row["provider"]: row for row in build_cost_report(since="all", by="agent", db_path=_recorded(tmp_path))["rows"]}
    assert rows["Explore"]["cache_hit_ratio"] == 0.0
    assert rows["main"]["cache_hit_ratio"] == 0.45


def test_a_report_without_usage_has_no_ratio(tmp_path: Path) -> None:
    report = build_cost_report(since="all", by="model", db_path=tmp_path / "missing.sqlite")
    assert report["rows"] == [] and report["cache_hit_ratio"] is None


def test_an_invalid_window_still_has_the_field(tmp_path: Path) -> None:
    assert build_cost_report(since="nonsense", by="model", db_path=tmp_path / "x.sqlite")["cache_hit_ratio"] is None


def test_the_json_output_has_the_ratios(tmp_path: Path) -> None:
    _recorded(tmp_path)
    result = CliRunner().invoke(app, ["cost", "--json", "--since", "all", "--by", "agent"], env={"NERDVANA_DATA_HOME": str(tmp_path)}, catch_exceptions=False)
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["cache_hit_ratio"] == 0.225
    assert {row["provider"]: row["cache_hit_ratio"] for row in data["rows"]} == {"main": 0.45, "Explore": 0.0}


def test_the_table_has_a_hit_percentage_column(tmp_path: Path) -> None:
    _recorded(tmp_path)
    result = CliRunner().invoke(app, ["cost", "--since", "all", "--by", "agent"], env={"NERDVANA_DATA_HOME": str(tmp_path), "COLUMNS": "200"}, catch_exceptions=False)
    assert result.exit_code == 0, result.output
    assert "Hit %" in result.output
    assert "45.0%" in result.output and "22.5%" in result.output and "0.0%" in result.output
