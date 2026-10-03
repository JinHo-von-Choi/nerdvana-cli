"""Service fees."""

import rates


def service_fee(amount: float) -> float:
    """Service fee on amount, rounded to cents."""
    return round(amount * rates.RATE, 2)
