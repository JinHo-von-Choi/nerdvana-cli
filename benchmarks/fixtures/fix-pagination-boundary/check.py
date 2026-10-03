"""Verify command for fix-pagination-boundary."""

from paginate import get_page, page_count

cases = {(0, 5): 0, (1, 5): 1, (5, 5): 1, (6, 5): 2, (10, 5): 2, (11, 5): 3, (7, 1): 7}
for (total, size), expected in cases.items():
    assert page_count(total, size) == expected, ((total, size), page_count(total, size))
assert get_page(list(range(11)), 3, 5) == [10]
assert get_page(list(range(11)), 4, 5) == []
print("ok")
