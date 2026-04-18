"""Tests for PricingTable — YAML loading + cost estimation."""
from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def table():
    from nerdvana_cli.core.analytics import PricingTable
    return PricingTable()


class TestPricingTableLoad:
    def test_known_providers(self, table) -> None:
        providers = table.known_providers()
        assert "anthropic" in providers
        assert "openai"    in providers
        assert "google"    in providers

    def test_known_models_anthropic(self, table) -> None:
        models = table.known_models("anthropic")
        assert len(models) >= 3

    def test_nonexistent_yaml(self, tmp_path: Path) -> None:
        from nerdvana_cli.core.analytics import PricingTable
        t = PricingTable(pricing_path=tmp_path / "missing.yml")
        # Should not raise; all costs default to 0
        cost = t.estimate_cost("openai", "gpt-4o", 1000, 500)
        assert cost == 0.0


class TestCostEstimation:
    def test_anthropic_sonnet(self, table) -> None:
        # claude-sonnet-4-6: $3/1k input, $15/1k output
        cost = table.estimate_cost("anthropic", "claude-sonnet-4-6", 1000, 1000)
        assert abs(cost - 18.0) < 0.001  # 3 + 15

    def test_openai_gpt4o(self, table) -> None:
        # gpt-4o: $5/1k input, $15/1k output
        cost = table.estimate_cost("openai", "gpt-4o", 2000, 1000)
        assert abs(cost - 25.0) < 0.001  # 10 + 15

    def test_unknown_model_zero(self, table) -> None:
        cost = table.estimate_cost("anthropic", "claude-nonexistent", 1000, 1000)
        assert cost == 0.0

    def test_ollama_free(self, table) -> None:
        cost = table.estimate_cost("ollama", "default", 10000, 10000)
        assert cost == 0.0

    def test_case_insensitive_provider(self, table) -> None:
        cost_lower = table.estimate_cost("anthropic", "claude-sonnet-4-6", 1000, 0)
        cost_upper = table.estimate_cost("ANTHROPIC", "claude-sonnet-4-6", 1000, 0)
        assert abs(cost_lower - cost_upper) < 1e-9
