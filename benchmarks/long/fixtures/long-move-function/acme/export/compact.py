"""Compact money text for narrow tables."""

from __future__ import annotations


def format_money_compact(cents: int) -> str:
    """Whole units only, for example 1235 for 123456 cents."""
    return str(round(cents / 100))


def demo() -> str:
    return format_money_compact(123456)
