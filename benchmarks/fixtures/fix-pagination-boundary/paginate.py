"""Page arithmetic for list endpoints."""


def page_count(total_items: int, page_size: int) -> int:
    """Number of pages needed to show total_items, page_size items per page."""
    return total_items // page_size + (1 if total_items % page_size > 1 else 0)


def get_page(items: list, page: int, page_size: int) -> list:
    """Items on the given 1-based page; an empty list past the last page."""
    start = (page - 1) * page_size
    return items[start:start + page_size]
