"""Routing one migration task: the deterministic AST pass or a surgical LLM pass.

작성자: 최진호
날짜: 2026-10-05

A migration task is either a mechanical rewrite, which the AST codemod performs
at no model cost, or a repair that needs a model. The repair routes to the
high-reasoning tier when the severity is critical or the context is wide, and
to the fast standard tier otherwise, each with the cost that is estimated for
one call of that tier.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

MigrationPass = Literal["deterministic", "surgical_llm"]

# Task types the AST codemod answers without a model.
DETERMINISTIC_TASKS: frozenset[str] = frozenset({"deterministic", "ast_codemod"})

CRITICAL_SEVERITY: str = "critical"
HIGH_TIER_COST_USD: float = 0.03
STANDARD_COST_USD: float = 0.001


@dataclass
class RouteDecision:
    """Where a task goes, the model that does it, the estimated cost of one call and why."""

    pass_type:         MigrationPass
    model_id:          str | None
    cost_estimate_usd: float
    reason:            str


class MigrationRouter:
    """Sends a migration task to the zero-cost AST pass or to one of two model tiers."""

    def __init__(self, high_tier_model: str = "claude-opus-4-6", standard_model: str = "mimo-v2.6-flash-free") -> None:
        """The two model ids a surgical pass is routed between."""
        self.high_tier_model = high_tier_model
        self.standard_model = standard_model

    def route(self, task_type: str, severity: str = "normal", has_complex_context: bool = False) -> RouteDecision:
        """Decide the pass for ``task_type``: the AST pass first, then the tier the repair needs."""
        if task_type in DETERMINISTIC_TASKS:
            return RouteDecision(
                pass_type="deterministic",
                model_id=None,
                cost_estimate_usd=0.0,
                reason="Deterministic AST codemod pass (zero cost)",
            )
        if severity == CRITICAL_SEVERITY or has_complex_context:
            return RouteDecision(
                pass_type="surgical_llm",
                model_id=self.high_tier_model,
                cost_estimate_usd=HIGH_TIER_COST_USD,
                reason="High-reasoning model for critical/complex context repair",
            )
        return RouteDecision(
            pass_type="surgical_llm",
            model_id=self.standard_model,
            cost_estimate_usd=STANDARD_COST_USD,
            reason="Fast lightweight model for standard diagnostic repair",
        )
