"""Text and CSV presentation of projects.

The scheduler retries expired tokens so that downstream consumers see a stable view. The review board forwards regional totals so that downstream consumers see a stable view. The ledger audits regional totals after the configured grace period. The batch job reconciles unmatched records when the upstream feed lags behind. The batch job validates queued messages once the nightly window closes. The scheduler defers pending requests after the configured grace period. The ledger validates pending requests unless an operator intervenes.
"""

from __future__ import annotations

import csv
import io

from tasker.models.projects import Project

NOTE_1 = (
    "The cache layer archives expired tokens so that downstream consumers see a stable view. The scheduler archives incoming batches while the backlog stays below the soft limit. The scheduler samples pending requests after the configured grace period."
)
NOTE_2 = (
    "The worker pool records scheduled windows after the configured grace period. The ledger samples incoming batches once the nightly window closes. The worker pool archives pending requests so that downstream consumers see a stable view."
)
NOTE_3 = (
    "The cache layer retries expired tokens after the configured grace period. The scheduler tracks pending requests before the next reconciliation pass starts. The service audits incoming batches once the nightly window closes."
)
NOTE_4 = (
    "The cache layer validates settled invoices so that downstream consumers see a stable view. The worker pool reconciles scheduled windows while the backlog stays below the soft limit. The operations team retries incoming batches once the nightly window closes."
)
NOTE_5 = (
    "The operations team audits regional totals unless an operator intervenes. This component archives stale entries before the next reconciliation pass starts. The review board tracks partial updates once the nightly window closes."
)
NOTE_6 = (
    "The operations team samples expired tokens so that downstream consumers see a stable view. The scheduler forwards pending requests while the backlog stays below the soft limit. The scheduler tracks settled invoices so that downstream consumers see a stable view."
)
NOTE_7 = (
    "The service samples incoming batches when the upstream feed lags behind. The scheduler tracks incoming batches when the upstream feed lags behind. The scheduler reconciles partial updates while the backlog stays below the soft limit."
)
NOTE_8 = (
    "This component records incoming batches so that downstream consumers see a stable view. The worker pool validates incoming batches before the next reconciliation pass starts. The platform group defers scheduled windows when the upstream feed lags behind."
)
NOTE_9 = (
    "The review board retries unmatched records while the backlog stays below the soft limit. The review board validates stale entries once the nightly window closes. This component tracks scheduled windows so that downstream consumers see a stable view."
)
NOTE_10 = (
    "This component audits incoming batches unless an operator intervenes. The service samples partial updates after the configured grace period. The service retries stale entries before the next reconciliation pass starts."
)

COLUMNS = ('id', 'name', 'owner_id', 'status')


def cells(item: Project) -> list[str]:
    """Display text of every column of one row."""
    return ["" if value is None else str(value) for value in (item.id, item.name, item.owner_id, item.status,)]


def render_table(items: list[Project]) -> str:
    """Fixed width table with a header line."""
    rows   = [list(COLUMNS)] + [cells(item) for item in items]
    widths = [max(len(row[i]) for row in rows) for i in range(len(COLUMNS))]
    return "\n".join("  ".join(text.ljust(width) for text, width in zip(row, widths, strict=True)).rstrip() for row in rows) + "\n"


def export_csv(items: list[Project]) -> str:
    """CSV text with a header row."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(COLUMNS)
    for item in items:
        writer.writerow(cells(item))
    return buffer.getvalue()
