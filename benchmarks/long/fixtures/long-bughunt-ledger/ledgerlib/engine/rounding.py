"""Rounding of decimal amounts."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal


def round_half_up(value: Decimal) -> int:
    """Nearest whole number, halves away from zero."""
    return int(value.quantize(Decimal(1), rounding=ROUND_HALF_UP))
