"""Small arithmetic helpers."""


def sum_range(start: int, stop: int) -> int:
    """Sum of the integers from start to stop, both included."""
    total = 0
    for value in range(start, stop):
        total += value
    return total
