"""Rate lookup by day."""

from __future__ import annotations

from bisect import bisect_right
from decimal import Decimal

from ledgerlib.data.rates import BASE, RATES


def rate_on(currency: str, day: str) -> Decimal:
    """USD per one major unit of the currency on an ISO day.

    A rate is in force from its effective date, inclusive, until the next effective date.
    """
    if currency == BASE:
        return Decimal(1)
    entries = RATES[currency]
    dates   = [effective for effective, _ in entries]
    index   = bisect_right(dates, day) - 1
    if index < 0:
        raise LookupError(f"no rate for {currency} on {day}")
    return Decimal(entries[index][1])
