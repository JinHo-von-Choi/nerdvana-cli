"""Sharing a session's cost limit with the sub-agents it starts.

Author: 최진호
Date:   2026-10-03

``session.max_cost_usd`` bounds one session, but a sub-agent runs its own loop with its own
counters, so without help it could spend what the parent has no way to see. The parent hands each
sub-agent an envelope, a fixed share of what is still unspent, and the sub-agent stops at it. When
the sub-agent finishes, its actual spend is charged to the parent and the unused part is returned.
Reserving before the sub-agent starts keeps agents that run in parallel from promising the same
money twice.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# A share smaller than this (USD) buys no useful work; the sub-agent runs on it anyway and stops quickly.
MIN_ENVELOPE = 0.0001


@dataclass
class Envelope:
    """Money set aside for one sub-agent (or one group of them)."""

    amount:  float
    settled: bool = False


@dataclass
class Budget:
    """What a session may spend, what its sub-agents already spent, and what is promised to running ones."""

    limit:    float
    spent:    float = 0.0            # actual spend of finished sub-agents
    promised: float = 0.0            # envelopes handed out and not yet settled
    _open:    list[Envelope] = field(default_factory=list)

    def remaining(self, own_spend: float) -> float:
        """Unspent and unpromised money, given what the session itself has spent."""
        return max(self.limit - own_spend - self.spent - self.promised, 0.0)

    def reserve(self, fraction: float, own_spend: float) -> Envelope:
        """Set aside *fraction* of what remains for one sub-agent."""
        amount   = max(self.remaining(own_spend) * min(max(fraction, 0.0), 1.0), MIN_ENVELOPE)
        envelope = Envelope(amount)
        self.promised += amount
        self._open.append(envelope)
        return envelope

    def settle(self, envelope: Envelope, actual: float) -> None:
        """Charge the sub-agent's actual spend and return what it did not use."""
        if envelope.settled:
            return
        envelope.settled = True
        self.promised    = max(self.promised - envelope.amount, 0.0)
        self.spent      += max(actual, 0.0)
        if envelope in self._open:
            self._open.remove(envelope)
