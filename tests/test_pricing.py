"""Tests for PricingTable: YAML loading, lookup behaviour, cost arithmetic.

Cost arithmetic is checked against a synthetic rate table so that routine
pricing refreshes in providers/pricing.yml cannot turn these red. The
shipped table is still exercised, but only for wiring (does it load, does
a known model resolve), never for a pinned dollar amount.
"""
from __future__ import annotations

from pathlib import Path

import pytest

# Fixed synthetic rates. These exist only to make the multiplication and
# addition in estimate_cost observable. Rates are USD per 1M tokens, the
# unit the real table uses, and are deliberately unrelated to
# any real provider.
SYNTHETIC_PRICING = """\
acme:
  m1: {input_per_1m: 2.0, output_per_1m: 4.0}
  m2: {input_per_1m: 0.0, output_per_1m: 0.0}
"""

CLAUDE_5_MODELS = ("claude-opus-5", "claude-sonnet-5", "claude-fable-5-1")


@pytest.fixture
def table():
    """PricingTable backed by the shipped providers/pricing.yml."""
    from nerdvana_cli.core.analytics import PricingTable
    return PricingTable()


@pytest.fixture
def synthetic_table(tmp_path: Path):
    """PricingTable backed by fixed rates that no pricing refresh will move."""
    from nerdvana_cli.core.analytics import PricingTable
    pricing_path = tmp_path / "pricing.yml"
    pricing_path.write_text(SYNTHETIC_PRICING, encoding="utf-8")
    return PricingTable(pricing_path=pricing_path)


class TestPricingTableLoad:
    def test_known_providers(self, table) -> None:
        providers = table.known_providers()
        assert "anthropic" in providers
        assert "openai"    in providers
        assert "google"    in providers

    def test_known_models_anthropic(self, table) -> None:
        models = table.known_models("anthropic")
        assert len(models) >= 3

    def test_claude_5_models_present(self, table) -> None:
        """The Claude 5 generation must be mapped, whatever its rates are."""
        models = set(table.known_models("anthropic"))
        missing = [m for m in CLAUDE_5_MODELS if m not in models]
        assert not missing, f"unmapped Claude 5 models: {missing}"

    def test_nonexistent_yaml(self, tmp_path: Path) -> None:
        from nerdvana_cli.core.analytics import PricingTable
        t = PricingTable(pricing_path=tmp_path / "missing.yml")
        # Should not raise; all costs default to 0
        cost = t.estimate_cost("openai", "gpt-4o", 1000, 500)
        assert cost == 0.0


class TestCostEstimation:
    def test_cost_is_input_rate_plus_output_rate(self, synthetic_table) -> None:
        # acme/m1 bills 2.0 per 1M input and 4.0 per 1M output
        cost = synthetic_table.estimate_cost("acme", "m1", 1_000_000, 1_000_000)
        assert abs(cost - 6.0) < 0.001  # 2 + 4

    def test_cost_scales_with_token_count(self, synthetic_table) -> None:
        # 3M input at 2.0 plus 500k output at 4.0
        cost = synthetic_table.estimate_cost("acme", "m1", 3_000_000, 500_000)
        assert abs(cost - 8.0) < 0.001  # 6 + 2

    def test_case_insensitive_provider(self, synthetic_table) -> None:
        cost_lower = synthetic_table.estimate_cost("acme", "m1", 1_000_000, 0)
        cost_upper = synthetic_table.estimate_cost("ACME", "m1", 1_000_000, 0)
        assert abs(cost_lower - cost_upper) < 1e-9

    def test_unknown_model_zero(self, table) -> None:
        cost = table.estimate_cost("anthropic", "claude-nonexistent", 1000, 1000)
        assert cost == 0.0

    def test_ollama_free(self, table) -> None:
        cost = table.estimate_cost("ollama", "default", 10000, 10000)
        assert cost == 0.0


class TestShippedTableWiring:
    """The shipped table must resolve real models, without pinning amounts."""

    def test_known_model_cost_is_positive(self, table) -> None:
        for model in CLAUDE_5_MODELS:
            cost = table.estimate_cost("anthropic", model, 1000, 1000)
            assert cost > 0.0, f"{model} resolved to a zero cost"
