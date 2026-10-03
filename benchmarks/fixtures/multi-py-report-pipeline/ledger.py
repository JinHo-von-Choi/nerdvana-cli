"""Totals per category."""


def summarize(records: list[tuple[str, int]]) -> list[tuple[str, int]]:
    """(category, total cents) with the largest total first; equal totals are ordered by category name."""
    totals: dict[str, int] = {}
    for category, cents in records:
        totals[category] = totals.get(category, 0) + cents
    return sorted(totals.items(), key=lambda item: item[1])
