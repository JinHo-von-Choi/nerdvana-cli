"""Verify command for the sum-range task: exits non-zero when calc.sum_range is wrong."""

from calc import sum_range

assert sum_range(1, 4) == 10, sum_range(1, 4)
assert sum_range(5, 5) == 5, sum_range(5, 5)
assert sum_range(-2, 2) == 0, sum_range(-2, 2)
print("ok")
