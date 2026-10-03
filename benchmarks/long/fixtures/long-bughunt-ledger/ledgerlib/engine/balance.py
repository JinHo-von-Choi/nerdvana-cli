"""Running and closing balances in USD cents."""

from __future__ import annotations

from ledgerlib.engine.conversion import convert_minor
from ledgerlib.engine.fees import fee_for
from ledgerlib.model import Account, Transaction


def running_balance(account: Account) -> list[tuple[Transaction, int, int]]:
    """Each transaction in day order with its USD cents and the balance after it."""
    rows, total = [], 0
    for tx in sorted(account.transactions, key=lambda t: t.day):
        usd = convert_minor(tx.amount_minor, tx.currency, tx.day)
        total += usd - fee_for(usd)
        rows.append((tx, usd, total))
    return rows


def closing_balance(account: Account) -> int:
    """Balance after the last transaction, zero for an account without any."""
    rows = running_balance(account)
    return rows[-1][2] if rows else 0
