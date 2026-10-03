"""URL slug helper."""

import re


def slugify(text: str, max_length: int | None = None) -> str:
    """Lowercase text with every run of non-alphanumeric characters replaced by one hyphen, no leading or trailing hyphen.

    With max_length the result is cut at a word boundary so that it is at most
    max_length long; a first word that is itself too long is cut hard.
    """
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    if max_length is None or len(slug) <= max_length:
        return slug
    cut = slug[:max_length]
    if slug[max_length] != "-" and "-" in cut:
        cut = cut[:cut.rindex("-")]
    return cut.rstrip("-")
