"""Token and cost accounting of one session, and the cost and token limits it is held to.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

import logging
from collections import Counter
from collections.abc import Callable

from nerdvana_cli.core import signals
from nerdvana_cli.core.budget import Budget
from nerdvana_cli.core.config.settings import NerdvanaSettings
from nerdvana_cli.core.telemetry.analytics import AnalyticsWriter, CallOrigin, PricingTable
from nerdvana_cli.core.telemetry.cache_watch import CacheWatch

logger = logging.getLogger(__name__)


class RunLimits:
    """What the session used and spent, its sub-agents included, measured against ``session`` limits.

    *counts* is the session's own signal counter: a cache miss is counted there, and so are the
    signals of a finished sub-agent.
    """

    def __init__(
        self,
        settings:         NerdvanaSettings,
        counts:           Counter[str],
        pricing_table:    PricingTable | None = None,
        analytics_writer: AnalyticsWriter | None = None,
    ) -> None:
        self._settings          = settings
        self._counts            = counts
        self.pricing_table      = pricing_table or PricingTable()
        self.analytics_writer   = analytics_writer or AnalyticsWriter(pricing_table=self.pricing_table)
        self._cache_watch       = CacheWatch()
        self._budget: Budget | None = None
        self._cost_limit_warned = False
        self.input_tokens       = 0
        self.output_tokens      = 0
        self.cache_read_tokens  = 0
        self.cache_write_tokens = 0
        self.cost_usd           = 0.0

    @property
    def budget(self) -> Budget:
        """The session's cost limit as shared with its sub-agents (rebuilt when the limit changes)."""
        limit = self._settings.session.max_cost_usd
        if self._budget is None or self._budget.limit != limit:
            self._budget = Budget(limit=limit)
        return self._budget

    def record(self, usage: dict[str, int], origin: CallOrigin, signal_summary: Callable[[], dict[str, int]]) -> float:
        """Add one request's reported *usage* to the totals, price it, and watch the prompt cache.

        Returns the request's estimated cost in USD.
        """
        model = self._settings.model
        cost  = self.record_other(model.provider, model.model, usage, origin)
        if self._cache_watch.observe(model.model, usage, signal_summary()):
            self._counts[signals.CACHE_MISS] += 1
        return cost

    def record_other(self, provider: str, model: str, usage: dict[str, int], origin: CallOrigin) -> float:
        """Add a request to the totals, the ledger and the session's cost under the model that served it.

        ``record`` is this for the session's own model; the advisor's requests come here directly. Returns
        the request's estimated cost in USD.
        """
        self._add_tokens(usage)
        cost = self.analytics_writer.record_api_call(provider, model, usage, origin)
        self.cost_usd += cost
        return cost

    def _add_tokens(self, usage: dict[str, int]) -> None:
        self.input_tokens       += usage.get("input_tokens", 0)
        self.output_tokens      += usage.get("output_tokens", 0)
        self.cache_read_tokens  += usage.get("cache_read_tokens", 0)
        self.cache_write_tokens += usage.get("cache_write_tokens", 0)

    def record_auxiliary(self, usage: dict[str, int], origin: CallOrigin, provider: str, model: str) -> float:
        """Add a request that is not the agent's own (the action classifier's) on *provider* and *model* to the totals.

        It is priced for the model it ran on, counts toward the cost and token limits, and leaves the
        prompt-cache watch alone: that watch follows the session model's own requests. Returns the cost in USD.
        """
        self.input_tokens  += usage.get("input_tokens", 0)
        self.output_tokens += usage.get("output_tokens", 0)
        cost = self.analytics_writer.record_api_call(provider, model, usage, origin)
        self.cost_usd += cost
        return cost

    def usage_summary(self) -> dict[str, int]:
        """Token totals for every provider request made so far in this session."""
        return {
            "input_tokens":       self.input_tokens,
            "output_tokens":      self.output_tokens,
            "cache_read_tokens":  self.cache_read_tokens,
            "cache_write_tokens": self.cache_write_tokens,
        }

    def absorb_subagent(self, usage: dict[str, int], signal_counts: dict[str, int]) -> None:
        """Add a finished sub-agent's token totals and signal counts to this session's own."""
        self._add_tokens(usage)
        self._counts.update(signal_counts)

    def total_cost_usd(self) -> float:
        """What the session spent: its own requests plus what its finished sub-agents spent."""
        return self.cost_usd + self.budget.spent

    def over_cost_limit(self) -> str:
        """The stop notice when ``session.max_cost_usd`` is spent, else an empty string."""
        limit = self._settings.session.max_cost_usd
        if limit <= 0:
            return ""
        spent = self.total_cost_usd()
        if spent < limit:
            return ""
        return f"\n[bold yellow]Cost limit reached (${spent:.4f} of ${limit:.2f}). Stopping.[/bold yellow]"

    def over_token_limit(self) -> str:
        """The stop notice when ``session.max_total_tokens`` is used up, else an empty string."""
        limit = self._settings.session.max_total_tokens
        used  = self.input_tokens + self.output_tokens
        if limit <= 0 or used < limit:
            return ""
        return f"\n[bold yellow]Token limit reached ({used:,} of {limit:,}). Stopping.[/bold yellow]"

    def exhausted(self) -> tuple[str, str]:
        """(stop reason, notice) for the first spent limit, cost before tokens; empty strings when none is."""
        for stop, notice in (("max_cost", self.over_cost_limit()), ("max_total_tokens", self.over_token_limit())):
            if notice:
                return stop, notice
        return "", ""

    def unpriced(self) -> tuple[str, str]:
        """(stop reason, notice) once per session when a cost limit is set but the model has no known price.

        With ``session.require_price`` the run is refused (stop reason ``unpriced``); otherwise the stop
        reason is empty and the notice only warns. Both are empty when nothing is to be said.
        """
        session, model = self._settings.session, self._settings.model
        if not self._cost_limit_unenforceable():
            return "", ""
        if session.require_price:
            return "unpriced", (
                f"\n[bold red]Cost limit ${session.max_cost_usd:.2f} cannot be enforced: no price is known for "
                f"{model.provider}/{model.model}. Refusing to run (session.require_price).[/bold red]"
            )
        return "", (
            f"\n[yellow]Cost limit ${session.max_cost_usd:.2f} is not enforced: "
            f"no price is known for {model.provider}/{model.model}.[/yellow]\n"
        )

    def _cost_limit_unenforceable(self) -> bool:
        """True once per session when a cost limit is set but the model has no known price."""
        if self._cost_limit_warned or self._settings.session.max_cost_usd <= 0:
            return False
        provider = self._settings.model.provider or ""
        model    = self._settings.model.model or ""
        if self.pricing_table.has_price(provider, model):
            return False
        self._cost_limit_warned = True
        logger.warning("cost limit set but %s/%s has no price; only the turn limit applies", provider, model)
        return True

    def record_session_totals(self) -> None:
        """Refresh the analytics session row with cumulative tokens and cost.

        Called at the end of every turn: the loop has no shutdown of its own,
        so the row is kept current rather than written once at exit.
        """
        self.analytics_writer.end_session(
            token_total        = self.input_tokens + self.output_tokens,
            cost_total         = self.cost_usd,
            cache_read_tokens  = self.cache_read_tokens,
            cache_write_tokens = self.cache_write_tokens,
        )
