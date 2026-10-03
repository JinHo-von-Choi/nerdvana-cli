"""Tag list helpers."""


def add_tag(tag: str, tags: list[str] = []) -> list[str]:
    """Return a new list holding the given tags followed by tag. The tags argument is left unchanged."""
    tags.append(tag)
    return tags
