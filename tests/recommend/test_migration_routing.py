"""Unit tests for nerdvana_cli.core.recommend.migration_router.

Covers the zero-cost deterministic routing, the high-reasoning tier for
critical or wide-context repairs and the standard tier for ordinary ones.

작성자: 최진호
날짜: 2026-10-05
"""

from __future__ import annotations

from nerdvana_cli.core.recommend.migration_router import MigrationRouter


def test_deterministic_and_ast_codemod_tasks_cost_nothing_and_name_no_model() -> None:
    router = MigrationRouter()
    for task_type in ("deterministic", "ast_codemod"):
        decision = router.route(task_type)
        assert decision.pass_type == "deterministic"
        assert decision.model_id is None
        assert decision.cost_estimate_usd == 0.0
        assert decision.reason == "Deterministic AST codemod pass (zero cost)"


def test_a_critical_task_is_routed_to_the_high_tier_model() -> None:
    router = MigrationRouter()
    decision = router.route("surgical_llm", severity="critical")
    assert decision.pass_type == "surgical_llm"
    assert decision.model_id == router.high_tier_model
    assert decision.cost_estimate_usd == 0.03
    assert decision.reason == "High-reasoning model for critical/complex context repair"


def test_a_task_with_complex_context_is_routed_to_the_high_tier_model() -> None:
    router = MigrationRouter()
    decision = router.route("surgical_llm", has_complex_context=True)
    assert decision.model_id == router.high_tier_model
    assert decision.cost_estimate_usd == 0.03


def test_an_ordinary_diagnostic_repair_is_routed_to_the_standard_model() -> None:
    router = MigrationRouter()
    decision = router.route("surgical_llm")
    assert decision.pass_type == "surgical_llm"
    assert decision.model_id == router.standard_model
    assert decision.cost_estimate_usd == 0.001
    assert decision.reason == "Fast lightweight model for standard diagnostic repair"


def test_the_default_tiers_are_the_documented_models() -> None:
    router = MigrationRouter()
    assert router.high_tier_model == "claude-opus-4-6"
    assert router.standard_model == "mimo-v2.6-flash-free"


def test_the_tiers_can_be_named_by_the_caller() -> None:
    router = MigrationRouter(high_tier_model="claude-opus-4-7", standard_model="kimi-k2")
    assert router.route("surgical_llm", severity="critical").model_id == "claude-opus-4-7"
    assert router.route("surgical_llm").model_id == "kimi-k2"


def test_the_deterministic_pass_wins_even_when_the_severity_is_critical() -> None:
    decision = MigrationRouter().route("ast_codemod", severity="critical", has_complex_context=True)
    assert decision.pass_type == "deterministic"
    assert decision.model_id is None
    assert decision.cost_estimate_usd == 0.0
