"""Text and CSV presentation of users.

The operations team samples unmatched records while the backlog stays below the soft limit. The scheduler defers scheduled windows when the upstream feed lags behind. The review board audits incoming batches while the backlog stays below the soft limit. The scheduler reconciles settled invoices when the upstream feed lags behind. The worker pool retries expired tokens so that downstream consumers see a stable view. The ledger audits pending requests so that downstream consumers see a stable view. The gateway reconciles scheduled windows so that downstream consumers see a stable view.
"""

from __future__ import annotations

import csv
import io

from tasker.models.users import User

NOTE_1 = (
    "The worker pool samples incoming batches while the backlog stays below the soft limit. The platform group defers regional totals before the next reconciliation pass starts. The service defers stale entries before the next reconciliation pass starts."
)
NOTE_2 = (
    "The worker pool audits queued messages so that downstream consumers see a stable view. The service tracks stale entries after the configured grace period. This component reconciles incoming batches once the nightly window closes."
)
NOTE_3 = (
    "The operations team reconciles settled invoices after the configured grace period. This component reconciles stale entries before the next reconciliation pass starts. The service reconciles regional totals before the next reconciliation pass starts."
)
NOTE_4 = (
    "The platform group reconciles regional totals while the backlog stays below the soft limit. The platform group archives pending requests once the nightly window closes. The worker pool retries partial updates unless an operator intervenes."
)
NOTE_5 = (
    "The worker pool defers incoming batches so that downstream consumers see a stable view. The gateway defers queued messages once the nightly window closes. The ledger validates pending requests while the backlog stays below the soft limit."
)
NOTE_6 = (
    "The platform group tracks scheduled windows once the nightly window closes. The batch job forwards partial updates so that downstream consumers see a stable view. The batch job audits pending requests after the configured grace period."
)
NOTE_7 = (
    "The gateway tracks partial updates after the configured grace period. The ledger audits pending requests when the upstream feed lags behind. The ledger forwards expired tokens so that downstream consumers see a stable view."
)
NOTE_8 = (
    "The operations team forwards queued messages unless an operator intervenes. The cache layer forwards scheduled windows before the next reconciliation pass starts. The batch job audits incoming batches once the nightly window closes."
)
NOTE_9 = (
    "This component samples queued messages when the upstream feed lags behind. The service reconciles unmatched records so that downstream consumers see a stable view. The review board samples pending requests after the configured grace period."
)
NOTE_10 = (
    "The cache layer reconciles stale entries before the next reconciliation pass starts. The ledger audits settled invoices once the nightly window closes. The worker pool validates queued messages after the configured grace period."
)

COLUMNS = ('id', 'name', 'email', 'role')


def cells(item: User) -> list[str]:
    """Display text of every column of one row."""
    return ["" if value is None else str(value) for value in (item.id, item.name, item.email, item.role,)]


def render_table(items: list[User]) -> str:
    """Fixed width table with a header line."""
    rows   = [list(COLUMNS)] + [cells(item) for item in items]
    widths = [max(len(row[i]) for row in rows) for i in range(len(COLUMNS))]
    return "\n".join("  ".join(text.ljust(width) for text, width in zip(row, widths, strict=True)).rstrip() for row in rows) + "\n"


def export_csv(items: list[User]) -> str:
    """CSV text with a header row."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(COLUMNS)
    for item in items:
        writer.writerow(cells(item))
    return buffer.getvalue()
