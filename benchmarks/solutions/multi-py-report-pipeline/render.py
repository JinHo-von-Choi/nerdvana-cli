"""Text output for the report."""


def format_cents(cents: int) -> str:
    """Cents as a decimal amount with exactly two fraction digits, e.g. 705 -> '7.05'."""
    return f"{cents // 100}.{cents % 100:02d}"


def render_report(summary: list[tuple[str, int]]) -> str:
    """One 'category: amount' line per entry."""
    return "\n".join(f"{category}: {format_cents(cents)}" for category, cents in summary)
