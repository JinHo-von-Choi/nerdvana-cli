"""The parts the agent loop delegates to: accounting and limits, typed-ahead input, edit checks and history helpers.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from nerdvana_cli.core.config.settings import NerdvanaSettings
from nerdvana_cli.core.context.loop_context import background_reports, provider_messages
from nerdvana_cli.core.input_queue import InputQueue
from nerdvana_cli.core.run_limits import RunLimits
from nerdvana_cli.core.safety.edit_guard import applies_edit, check_edit_scope, edit_targets
from nerdvana_cli.core.state import signals
from nerdvana_cli.core.telemetry.analytics import AnalyticsWriter, CallOrigin, PricingTable
from nerdvana_cli.core.tool import ToolContext
from nerdvana_cli.types import Message, Role

PRICING = """\
acme:
  priced: {input_per_1m: 1000000.0, output_per_1m: 1000000.0}
"""


def _limits(tmp_path: Path, model: str = "priced", **session: Any) -> tuple[RunLimits, Counter[str]]:
    pricing = tmp_path / "pricing.yml"
    pricing.write_text(PRICING, encoding="utf-8")
    settings = NerdvanaSettings()
    settings.model.provider, settings.model.model = "acme", model
    for key, value in session.items():
        setattr(settings.session, key, value)
    table  = PricingTable(pricing_path=pricing)
    counts: Counter[str] = Counter()
    writer = AnalyticsWriter(db_path=tmp_path / "an.sqlite", pricing_table=table, enabled=False)
    return RunLimits(settings, counts, table, writer), counts


def test_recorded_usage_is_totalled_and_priced(tmp_path: Path) -> None:
    limits, _ = _limits(tmp_path)
    cost = limits.record({"input_tokens": 2, "output_tokens": 1, "cache_read_tokens": 1}, CallOrigin(), dict)
    assert cost == pytest.approx(3.0) and limits.cost_usd == pytest.approx(3.0)
    assert limits.usage_summary() == {"input_tokens": 2, "output_tokens": 1, "cache_read_tokens": 1, "cache_write_tokens": 0}


def test_a_spent_cost_limit_stops_before_the_token_limit(tmp_path: Path) -> None:
    limits, _ = _limits(tmp_path, max_cost_usd=1.0, max_total_tokens=1)
    assert limits.exhausted() == ("", "")
    limits.record({"input_tokens": 5}, CallOrigin(), dict)
    stop, notice = limits.exhausted()
    assert stop == "max_cost" and "Cost limit reached" in notice


def test_the_token_limit_counts_what_sub_agents_used(tmp_path: Path) -> None:
    limits, counts = _limits(tmp_path, max_total_tokens=10)
    limits.absorb_subagent({"input_tokens": 6, "output_tokens": 4}, {signals.TOOL_ERROR: 2})
    assert limits.exhausted()[0] == "max_total_tokens"
    assert counts[signals.TOOL_ERROR] == 2


def test_an_unpriced_model_is_reported_once_and_refused_only_when_a_price_is_required(tmp_path: Path) -> None:
    limits, _ = _limits(tmp_path, model="unknown", max_cost_usd=1.0)
    stop, notice = limits.unpriced()
    assert stop == "" and "is not enforced" in notice
    assert limits.unpriced() == ("", "")
    strict, _ = _limits(tmp_path, model="unknown", max_cost_usd=1.0, require_price=True)
    stop, notice = strict.unpriced()
    assert stop == "unpriced" and "Refusing to run" in notice


def test_typed_ahead_text_comes_out_in_order_and_once() -> None:
    queue = InputQueue()
    queue.put("first")
    queue.put("   ")
    queue.put("second")
    assert queue.pending()
    assert queue.take() == ["first", "second"]
    assert not queue.pending() and queue.take() == []


@dataclass
class _Edit:
    file_path: str
    apply:     bool = True


def test_edit_targets_and_whether_an_edit_applies() -> None:
    assert edit_targets(_Edit("a.py")) == ["a.py"]
    assert edit_targets(_Edit("  ")) == []
    assert applies_edit("FileEdit", _Edit("a.py"))
    assert not applies_edit("FileEdit", _Edit("a.py", apply=False))
    assert not applies_edit("FileRead", _Edit("a.py"))


def test_an_edit_outside_the_edit_scope_is_refused(tmp_path: Path) -> None:
    context = ToolContext(cwd=str(tmp_path))
    context.state["edit_scope"] = ["src"]
    inside  = check_edit_scope({"id": "1", "name": "FileEdit"}, _Edit("src/a.py"), context)
    outside = check_edit_scope({"id": "2", "name": "FileEdit"}, _Edit("docs/a.md"), context)
    assert inside is None
    assert outside is not None and outside.is_error and "Outside this agent's edit scope (src): docs/a.md" in outside.content


def test_the_provider_form_keeps_tool_calls_results_and_blocks() -> None:
    history = [
        Message(role=Role.USER, content="go"),
        Message(role=Role.ASSISTANT, content="", tool_uses=[{"id": "t", "name": "X", "input": {}}], provider_blocks=[{"type": "b"}]),
        Message(role=Role.TOOL, content="ok", tool_use_id="t"),
    ]
    assert provider_messages(history) == [
        {"role": "user", "content": "go"},
        {"role": "assistant", "content": "", "tool_uses": [{"id": "t", "name": "X", "input": {}}], "provider_blocks": [{"type": "b"}]},
        {"role": "tool", "content": "ok", "tool_use_id": "t", "is_error": False},
    ]


def test_finished_background_work_is_reported_with_long_output_cut() -> None:
    @dataclass
    class _Task:
        id:          str
        status:      str
        description: str
        output:      str
        error:       str = ""

    class _Registry:
        def drain_unreported(self) -> list[_Task]:
            return [_Task("b1", "completed", "build", "x" * 5_000)]

    (report,) = background_reports(_Registry())
    assert report.content.startswith("[Background task b1 completed] build\n")
    assert "[cut; TaskGet b1 returns the full output]" in report.content
    assert background_reports(None) == []
