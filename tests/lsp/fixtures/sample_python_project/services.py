"""Sample services module for LSP integration tests.

Uses models.User and models.Product to test cross-file reference tracking.
"""
from __future__ import annotations

from tests.lsp.fixtures.sample_python_project.models import Product, User


def greet_user(user: User) -> str:
    """Call User.greeting and prefix with 'Welcome: '."""
    return "Welcome: " + user.greeting()


def checkout(user: User, product: Product, discount: float = 0.0) -> dict[str, object]:
    """Process a checkout."""
    final_price = product.discounted(discount)
    return {
        "user":    user.name,
        "product": product.title,
        "price":   final_price,
    }


def calculate_total(products: list[Product]) -> float:
    """Sum prices of all products."""
    return sum(p.price for p in products)
