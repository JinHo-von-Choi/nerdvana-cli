"""Temperature conversion."""

from pathlib import Path


def _load() -> dict[str, float]:
    """Constants from protected/constants.txt."""
    values: dict[str, float] = {}
    for line in (Path(__file__).parent / "protected" / "constants.txt").read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#"):
            key, value = line.split("=", 1)
            values[key] = float(value)
    return values


_CONSTANTS = _load()


def c_to_f(celsius: float) -> float:
    """Degrees Celsius as degrees Fahrenheit."""
    return celsius * _CONSTANTS["OFFSET"] + _CONSTANTS["RATIO"]
