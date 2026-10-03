"""Conversion of postings into USD cents."""

from __future__ import annotations

from decimal import Decimal

from ledgerlib.data.rates import BASE
from ledgerlib.engine.ratetable import rate_on
from ledgerlib.engine.rounding import round_half_up
from ledgerlib.money import exponent


def convert_minor(amount_minor: int, currency: str, day: str) -> int:
    """USD cents worth of an amount given in the minor units of a currency on a day.

    The amount is first turned into major units using the currency's own number of digits.
    """
    if currency == BASE:
        return amount_minor
    major = Decimal(amount_minor) / (10 ** exponent(currency))
    return round_half_up(major * rate_on(currency, day) * 100)
