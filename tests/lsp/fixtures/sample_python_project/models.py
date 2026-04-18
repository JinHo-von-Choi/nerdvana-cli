"""Sample models module for LSP integration tests."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class User:
    """Simple user entity."""

    name:  str
    email: str
    age:   int = 0

    def greeting(self) -> str:
        """Return a greeting string."""
        return f"Hello, {self.name}!"

    def is_adult(self) -> bool:
        """Return True if age >= 18."""
        return self.age >= 18


@dataclass
class Product:
    """Simple product entity."""

    title: str
    price: float

    def discounted(self, pct: float) -> float:
        """Apply percentage discount and return new price."""
        return self.price * (1.0 - pct / 100.0)
