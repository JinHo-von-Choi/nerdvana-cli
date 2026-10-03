"""Parse category,amount CSV text into records."""


def read_records(text: str) -> list[tuple[str, int]]:
    """(category, cents) for every data row; the first line is a header, blank lines are skipped.

    Categories are compared case-insensitively and ignore surrounding whitespace,
    so they are returned lowercased and stripped.
    """
    records: list[tuple[str, int]] = []
    for line in text.splitlines()[1:]:
        if not line.strip():
            continue
        category, amount = line.split(",")
        records.append((category, round(float(amount) * 100)))
    return records
