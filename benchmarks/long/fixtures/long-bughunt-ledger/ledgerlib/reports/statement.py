"""Plain text account statements."""

from __future__ import annotations

from ledgerlib.engine.balance import closing_balance, running_balance
from ledgerlib.model import Account
from ledgerlib.money import format_minor


def render_statement(account: Account) -> str:
    """Statement text with one line per transaction and the closing balance in USD."""
    lines = [f"Statement for {account.owner} ({account.account_id})"]
    for tx, usd, _ in running_balance(account):
        lines.append(f"{tx.day}  {tx.currency}  {format_minor(tx.amount_minor, tx.currency):>12}  USD {format_minor(usd, 'USD'):>10}")
    lines.append(f"Closing balance: USD {format_minor(closing_balance(account), 'USD')}")
    return "\n".join(lines) + "\n"
