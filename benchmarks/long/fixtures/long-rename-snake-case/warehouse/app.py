"""Resolve every route by name and evaluate it."""

from __future__ import annotations

import importlib

from warehouse.registry import ROUTES


def resolve(route: str):
    """The function a route names."""
    module_name, function_name = route.split(".")
    module = importlib.import_module(f"warehouse.{module_name}")
    return getattr(module, function_name)


def run(seed: int = 7) -> list[int]:
    """Evaluate every route once, each with its own input."""
    return [resolve(route)(seed + index) for index, route in enumerate(ROUTES)]


def main() -> None:
    print(",".join(str(value) for value in run()))


if __name__ == "__main__":
    main()
