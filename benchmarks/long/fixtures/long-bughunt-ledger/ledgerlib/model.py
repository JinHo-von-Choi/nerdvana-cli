"""Plain records of the ledger."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Transaction:
    """One posting in the currency's minor units (cents, or whole yen for JPY)."""

    day:          str
    currency:     str
    amount_minor: int
    memo:         str = ""


@dataclass
class Account:
    """An account whose statements are kept in USD."""

    account_id:   str
    owner:        str
    transactions: list[Transaction] = field(default_factory=list)
