"""Cache token accounting: cost, session totals and the analytics session row.

Costs are checked against a synthetic rate table so routine pricing refreshes
cannot turn these red.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

import sqlite3
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from nerdvana_cli.core.agent_loop import AgentLoop
from nerdvana_cli.core.config.settings import NerdvanaSettings
from nerdvana_cli.core.session import SessionStorage
from nerdvana_cli.core.telemetry.analytics import AnalyticsWriter, PricingTable
from nerdvana_cli.core.tool import ToolRegistry
from nerdvana_cli.providers.base import ProviderEvent

PRICING = """\
acme:
  cached: {input_per_1m: 10.0, output_per_1m: 40.0, cache_write_per_1m: 12.5, cache_read_per_1m: 1.0}
  plain:  {input_per_1m: 10.0, output_per_1m: 40.0}
  dear:   {input_per_1m: 100.0, output_per_1m: 400.0}
"""


@pytest.fixture()
def table(tmp_path: Path) -> PricingTable:
    path = tmp_path / "pricing.yml"
    path.write_text(PRICING, encoding="utf-8")
    return PricingTable(pricing_path=path)


def test_cached_and_fresh_input_are_billed_at_their_own_rates(table: PricingTable) -> None:
    # 1,000,000 prompt tokens: 600k read from cache, 100k written, 300k fresh; 100k output.
    cost = table.estimate_cost("acme", "cached", 1_000_000, 100_000, cache_read_tokens=600_000, cache_write_tokens=100_000)
    assert cost == pytest.approx(300_000 * 10 / 1e6 + 600_000 * 1 / 1e6 + 100_000 * 12.5 / 1e6 + 100_000 * 40 / 1e6)


def test_an_entry_without_cache_rates_bills_cached_tokens_as_plain_input(table: PricingTable) -> None:
    with_cache    = table.estimate_cost("acme", "plain", 1_000_000, 0, cache_read_tokens=900_000)
    without_cache = table.estimate_cost("acme", "plain", 1_000_000, 0)
    assert with_cache == pytest.approx(without_cache)


def test_cache_tokens_beyond_the_prompt_never_make_input_negative(table: PricingTable) -> None:
    cost = table.estimate_cost("acme", "cached", 100, 0, cache_read_tokens=500)
    assert cost == pytest.approx(500 * 1 / 1e6)


def test_old_call_signature_still_works(table: PricingTable) -> None:
    assert table.estimate_cost("acme", "plain", 1_000_000, 1_000_000) == pytest.approx(50.0)


def test_shipped_table_prices_a_cache_read_below_plain_input() -> None:
    """Wiring only: for the shipped Anthropic entries a cached read is cheaper."""
    shipped = PricingTable()
    plain   = shipped.estimate_cost("anthropic", "claude-sonnet-5-5", 1_000_000, 0)
    cached  = shipped.estimate_cost("anthropic", "claude-sonnet-5-5", 1_000_000, 0, cache_read_tokens=1_000_000)
    assert 0 < cached < plain


# ---------------------------------------------------------------------------
# Session row
# ---------------------------------------------------------------------------


def test_the_session_row_stores_cache_tokens(tmp_path: Path, table: PricingTable) -> None:
    db     = tmp_path / "a.sqlite"
    writer = AnalyticsWriter(db_path=db, pricing_table=table, enabled=True)
    writer.start_session("s1")
    writer.end_session(token_total=10, cost_total=0.5, cache_read_tokens=7, cache_write_tokens=2)
    row = sqlite3.connect(db).execute("SELECT cache_read_tokens, cache_write_tokens FROM sessions WHERE id='s1'").fetchone()
    assert row == (7, 2)


def test_a_database_from_before_cache_accounting_is_migrated(tmp_path: Path, table: PricingTable) -> None:
    db   = tmp_path / "old.sqlite"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY, started_at TEXT NOT NULL, ended_at TEXT, mode TEXT, context TEXT, token_total INTEGER DEFAULT 0, cost_total REAL DEFAULT 0.0)")
    conn.execute("INSERT INTO sessions (id, started_at) VALUES ('legacy', 'x')")
    conn.commit()
    conn.close()

    writer = AnalyticsWriter(db_path=db, pricing_table=table, enabled=True)
    writer.start_session("s2")
    writer.end_session(token_total=1, cost_total=0.1, cache_read_tokens=5)

    rows = dict(sqlite3.connect(db).execute("SELECT id, cache_read_tokens FROM sessions").fetchall())
    assert rows == {"legacy": 0, "s2": 5}


# ---------------------------------------------------------------------------
# Loop totals
# ---------------------------------------------------------------------------


class _Reporting:
    def __init__(self, usages: list[dict[str, int]]) -> None:
        self.usages = list(usages)

    async def stream(self, system_prompt: str, messages: Any, tools: Any) -> AsyncIterator[ProviderEvent]:
        yield ProviderEvent(type="content_delta", content="ok")
        yield ProviderEvent(type="usage", usage=self.usages.pop(0))
        yield ProviderEvent(type="done", stop_reason="end_turn")


def _loop(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, table: PricingTable, usages: list[dict[str, int]]) -> AgentLoop:
    monkeypatch.setenv("NERDVANA_DATA_HOME", str(tmp_path / "data"))
    provider = _Reporting(usages)
    monkeypatch.setattr(AgentLoop, "create_provider_from_settings", lambda self: provider)
    monkeypatch.setattr(AgentLoop, "build_system_prompt", lambda self: "system")
    settings                = NerdvanaSettings()
    settings.cwd            = str(tmp_path)
    settings.model.provider = "acme"
    settings.model.model    = "cached"
    return AgentLoop(
        settings         = settings,
        registry         = ToolRegistry(),
        session          = SessionStorage(session_id="cache", storage_dir=str(tmp_path / "sessions")),
        analytics_writer = AnalyticsWriter(db_path=tmp_path / "an.sqlite", pricing_table=table, enabled=True),
        pricing_table    = table,
    )


async def test_the_loop_accumulates_cache_tokens_and_prices_the_session(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, table: PricingTable,
) -> None:
    loop = _loop(monkeypatch, tmp_path, table, [
        {"input_tokens": 1000, "output_tokens": 10, "cache_write_tokens": 800},
        {"input_tokens": 1100, "output_tokens": 20, "cache_read_tokens": 900},
    ])
    async for _ in loop.run("one"):
        pass
    async for _ in loop.run("two"):
        pass

    assert loop.state.usage.cache_read_tokens == 900
    assert loop.state.usage.input_tokens == 1100
    assert loop.limits.cache_write_tokens == 800
    expected = table.estimate_cost("acme", "cached", 2100, 30, cache_read_tokens=900, cache_write_tokens=800)
    assert loop.session_cost_usd() == pytest.approx(expected)
    row = sqlite3.connect(tmp_path / "an.sqlite").execute("SELECT cache_read_tokens, cache_write_tokens, cost_total FROM sessions").fetchone()
    assert row[:2] == (900, 800)
    assert row[2] == pytest.approx(expected)


async def test_the_context_estimate_uses_the_whole_prompt_not_just_the_fresh_part(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, table: PricingTable,
) -> None:
    loop = _loop(monkeypatch, tmp_path, table, [{"input_tokens": 50_000, "output_tokens": 5, "cache_read_tokens": 49_000}])
    async for _ in loop.run("one"):
        pass
    assert loop._context_budget.current(loop.state.messages) >= 50_000


async def test_each_request_is_priced_for_the_model_that_served_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, table: PricingTable,
) -> None:
    loop = _loop(monkeypatch, tmp_path, table, [
        {"input_tokens": 1000, "output_tokens": 10},
        {"input_tokens": 1100, "output_tokens": 20},
    ])
    async for _ in loop.run("one"):
        pass
    loop.settings.model.model = "dear"
    async for _ in loop.run("two"):
        pass
    expected = table.estimate_cost("acme", "cached", 1000, 10) + table.estimate_cost("acme", "dear", 1100, 20)
    assert loop.session_cost_usd() == pytest.approx(expected)


async def test_the_total_adds_what_finished_sub_agents_spent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, table: PricingTable,
) -> None:
    loop = _loop(monkeypatch, tmp_path, table, [{"input_tokens": 1000, "output_tokens": 10}])
    async for _ in loop.run("one"):
        pass
    own = loop.session_cost_usd()
    loop.budget.spent += 0.25
    assert loop.session_cost_usd() == pytest.approx(own)
    assert loop.total_cost_usd() == pytest.approx(own + 0.25)
