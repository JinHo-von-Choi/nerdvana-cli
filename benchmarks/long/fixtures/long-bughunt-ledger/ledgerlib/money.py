"""Currency digits and plain text amounts."""

from __future__ import annotations

EXPONENTS = {"USD": 2, "EUR": 2, "GBP": 2, "JPY": 0, "KWD": 3}


def exponent(currency: str) -> int:
    """Number of minor-unit digits of a currency."""
    return EXPONENTS[currency]


def format_minor(amount_minor: int, currency: str) -> str:
    """Text of an amount given in minor units, with the currency's own number of decimals."""
    digits = exponent(currency)
    sign   = "-" if amount_minor < 0 else ""
    whole, fraction = divmod(abs(amount_minor), 10 ** digits)
    return f"{sign}{whole:,}" + (f".{fraction:0{digits}d}" if digits else "")
