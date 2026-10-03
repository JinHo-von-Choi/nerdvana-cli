"""Fees on large postings."""

from __future__ import annotations

from decimal import Decimal

FEE_FREE_BELOW = 100_000
FEE_RATE       = Decimal("0.0025")


def fee_for(usd_cents: int) -> int:
    """Fee in USD cents. Postings below 1000 USD carry none; above, a quarter of a percent, truncated by design."""
    if abs(usd_cents) < FEE_FREE_BELOW:
        return 0
    return int(abs(usd_cents) * FEE_RATE)
