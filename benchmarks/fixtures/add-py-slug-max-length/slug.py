"""URL slug helper."""

import re


def slugify(text: str) -> str:
    """Lowercase text with every run of non-alphanumeric characters replaced by one hyphen, no leading or trailing hyphen."""
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
