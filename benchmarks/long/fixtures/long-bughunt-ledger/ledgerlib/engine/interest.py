"""Interest tiers."""

from __future__ import annotations

from bisect import bisect_left

THRESHOLDS = [100_000, 1_000_000, 10_000_000]
RATES_BP   = [10, 25, 40, 55]


def tier_rate_bp(balance_cents: int) -> int:
    """Basis points earned at a balance. A balance equal to a threshold stays in the lower tier, by design."""
    return RATES_BP[bisect_left(THRESHOLDS, balance_cents)]
