"""General purpose helpers shared by the acme packages."""

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


def slugify(text: str) -> str:
    """Lower case text joined by single dashes."""
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def clamp(value: int, low: int, high: int) -> int:
    """Keep a value inside an inclusive range."""
    return max(low, min(high, value))


def chunked(items: list[int], size: int) -> list[list[int]]:
    """Split a list into consecutive chunks of at most the given size."""
    return [items[i:i + size] for i in range(0, len(items), size)]


def percent(part: int, whole: int) -> str:
    """A share as a percentage with one decimal, rounded half up."""
    if whole == 0:
        return "0.0%"
    value = (Decimal(part) * 100 / Decimal(whole)).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    return f"{value}%"


def truncate_words(text: str, limit: int) -> str:
    """Cut text to the given number of words, adding an ellipsis when something was dropped."""
    words = text.split()
    return " ".join(words[:limit]) + (" ..." if len(words) > limit else "")


def pluralize(count: int, noun: str) -> str:
    """Count with a naive plural."""
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


def parse_bool(text: str) -> bool:
    """Interpret common yes and no spellings."""
    return text.strip().lower() in {"1", "true", "yes", "on"}


def pad_left(text: str, width: int, fill: str = " ") -> str:
    """Right align text in a field."""
    return text.rjust(width, fill)


def flatten(rows: list[list[int]]) -> list[int]:
    """One list from a list of lists."""
    return [value for row in rows for value in row]


def median(values: list[int]) -> int:
    """Middle value of a non-empty list, the lower one for even lengths."""
    ordered = sorted(values)
    return ordered[(len(ordered) - 1) // 2]


def roman(number: int) -> str:
    """Roman numeral for 1 to 3999."""
    out = ""
    for value, glyph in ((1000, "M"), (900, "CM"), (500, "D"), (400, "CD"), (100, "C"), (90, "XC"), (50, "L"), (40, "XL"), (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I")):
        while number >= value:
            out += glyph
            number -= value
    return out


def initials(name: str) -> str:
    """First letters of each word, upper case."""
    return "".join(word[0].upper() for word in name.split())


def describe_price(cents: int, currency: str = "USD") -> str:
    """A price with its currency spelled out for plain text channels."""
    return f"{format_money(cents, currency)} ({currency})"
