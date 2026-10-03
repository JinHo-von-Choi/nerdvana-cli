"""Sales tax."""

import rates


def tax(amount: float) -> float:
    """Sales tax on amount, rounded to cents."""
    return round(amount * rates.TAX_RATE, 2)
