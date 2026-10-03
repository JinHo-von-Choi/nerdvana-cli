"""Run the demo of every module and join the results."""

from __future__ import annotations

import importlib
import pkgutil

import acme


def modules() -> list[str]:
    """Names of all acme modules that define demo(), in sorted order."""
    found = []
    for info in pkgutil.walk_packages(acme.__path__, "acme."):
        if not info.ispkg and hasattr(importlib.import_module(info.name), "demo"):
            found.append(info.name)
    return sorted(found)


def run_all() -> str:
    """One text with the demo output of every module."""
    return "\n".join(f"{name}: {importlib.import_module(name).demo()}" for name in modules())


if __name__ == "__main__":
    print(run_all())
