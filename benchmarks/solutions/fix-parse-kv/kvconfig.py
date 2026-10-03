"""Tiny key = value config parser."""


def parse(text: str) -> dict[str, str]:
    """Parse 'key = value' lines into a dict.

    Blank lines and lines starting with '#' are skipped. Only the first '=' on a
    line separates the key from the value; whitespace around both is dropped.
    """
    result: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, value = line.split("=", 1)
        result[key.strip()] = value.strip()
    return result
