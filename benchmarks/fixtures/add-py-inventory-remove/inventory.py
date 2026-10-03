"""A minimal stock ledger."""


class Inventory:
    """Item name to quantity in stock."""

    def __init__(self) -> None:
        self._stock: dict[str, int] = {}

    def add(self, name: str, qty: int) -> int:
        """Raise the stock of name by qty and return the new quantity. qty must be positive."""
        if qty <= 0:
            raise ValueError("qty must be positive")
        self._stock[name] = self._stock.get(name, 0) + qty
        return self._stock[name]

    def count(self, name: str) -> int:
        """Quantity in stock; 0 for an unknown item."""
        return self._stock.get(name, 0)
