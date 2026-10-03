"""Money formatting and parsing."""

from __future__ import annotations

import re
from decimal import ROUND_HALF_UP, Decimal


CURRENCY_SYMBOLS = {"USD": "$", "EUR": "EUR ", "GBP": "GBP ", "JPY": "JPY "}


def format_money(cents: int, currency: str = "USD") -> str:
    """Format an amount in minor units, for example 123456 USD as $1,234.56."""
    sign = "-" if cents < 0 else ""
    whole, fraction = divmod(abs(cents), 100)
    return f"{sign}{CURRENCY_SYMBOLS.get(currency, currency + ' ')}{whole:,}.{fraction:02d}"


def parse_money(text: str) -> int:
    """Read an amount such as $1,234.56 or -EUR 7.5 back into minor units."""
    cleaned = re.sub(r"[^0-9.\-]", "", text)
    return int((Decimal(cleaned) * 100).to_integral_value(rounding=ROUND_HALF_UP))
