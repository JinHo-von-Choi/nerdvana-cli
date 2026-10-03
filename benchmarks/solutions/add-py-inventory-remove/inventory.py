"""A minimal stock ledger."""


class InsufficientStock(Exception):
    """Raised when a removal asks for more than is in stock."""


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

    def remove(self, name: str, qty: int) -> int:
        """Lower the stock of name by qty and return the remaining quantity."""
        if qty <= 0:
            raise ValueError("qty must be positive")
        if self.count(name) < qty:
            raise InsufficientStock(f"{name}: have {self.count(name)}, need {qty}")
        self._stock[name] -= qty
        return self._stock[name]

    def count(self, name: str) -> int:
        """Quantity in stock; 0 for an unknown item."""
        return self._stock.get(name, 0)
