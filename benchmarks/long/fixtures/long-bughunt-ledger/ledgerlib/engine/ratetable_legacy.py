"""Earlier rate lookup, kept for the migration scripts."""

from __future__ import annotations

from bisect import bisect_left
from decimal import Decimal

from ledgerlib.data.rates import RATES


def rate_on_legacy(currency: str, day: str) -> Decimal:
    """Rate in force before the day (exclusive). Only the migration scripts use this."""
    # FIXME: boundary handling differs from ratetable.rate_on on purpose, see the migration notes.
    entries = RATES[currency]
    index   = bisect_left([d for d, _ in entries], day) - 1
    return Decimal(entries[max(index, 0)][1])
