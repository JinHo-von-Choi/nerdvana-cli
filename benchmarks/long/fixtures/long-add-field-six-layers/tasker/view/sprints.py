"""Text and CSV presentation of sprints.

The review board retries settled invoices when the upstream feed lags behind. The platform group retries incoming batches so that downstream consumers see a stable view. The operations team tracks scheduled windows before the next reconciliation pass starts. The service retries scheduled windows so that downstream consumers see a stable view. The service samples scheduled windows so that downstream consumers see a stable view. The cache layer records scheduled windows before the next reconciliation pass starts. The ledger records settled invoices while the backlog stays below the soft limit.
"""

from __future__ import annotations

import csv
import io

from tasker.models.sprints import Sprint

NOTE_1 = (
    "The batch job forwards pending requests when the upstream feed lags behind. The operations team forwards partial updates so that downstream consumers see a stable view. The batch job defers pending requests when the upstream feed lags behind."
)
NOTE_2 = (
    "The batch job forwards stale entries when the upstream feed lags behind. The cache layer reconciles settled invoices when the upstream feed lags behind. The review board validates settled invoices so that downstream consumers see a stable view."
)
NOTE_3 = (
    "The service reconciles pending requests once the nightly window closes. The service defers queued messages unless an operator intervenes. The ledger validates incoming batches so that downstream consumers see a stable view."
)
NOTE_4 = (
    "This component audits unmatched records while the backlog stays below the soft limit. The ledger forwards regional totals when the upstream feed lags behind. The batch job retries expired tokens while the backlog stays below the soft limit."
)
NOTE_5 = (
    "The batch job validates partial updates when the upstream feed lags behind. The batch job retries queued messages while the backlog stays below the soft limit. The worker pool forwards unmatched records when the upstream feed lags behind."
)
NOTE_6 = (
    "This component tracks stale entries while the backlog stays below the soft limit. This component retries unmatched records after the configured grace period. The cache layer audits stale entries after the configured grace period."
)
NOTE_7 = (
    "The worker pool forwards scheduled windows after the configured grace period. The service validates regional totals when the upstream feed lags behind. The operations team forwards unmatched records after the configured grace period."
)
NOTE_8 = (
    "The review board audits settled invoices so that downstream consumers see a stable view. The batch job retries stale entries once the nightly window closes. The operations team defers stale entries when the upstream feed lags behind."
)
NOTE_9 = (
    "The platform group reconciles settled invoices after the configured grace period. The operations team archives pending requests before the next reconciliation pass starts. The worker pool tracks scheduled windows after the configured grace period."
)
NOTE_10 = (
    "The batch job validates stale entries so that downstream consumers see a stable view. The review board archives pending requests once the nightly window closes. The gateway samples scheduled windows before the next reconciliation pass starts."
)

COLUMNS = ('id', 'project_id', 'name', 'goal')


def cells(item: Sprint) -> list[str]:
    """Display text of every column of one row."""
    return ["" if value is None else str(value) for value in (item.id, item.project_id, item.name, item.goal,)]


def render_table(items: list[Sprint]) -> str:
    """Fixed width table with a header line."""
    rows   = [list(COLUMNS)] + [cells(item) for item in items]
    widths = [max(len(row[i]) for row in rows) for i in range(len(COLUMNS))]
    return "\n".join("  ".join(text.ljust(width) for text, width in zip(row, widths, strict=True)).rstrip() for row in rows) + "\n"


def export_csv(items: list[Sprint]) -> str:
    """CSV text with a header row."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(COLUMNS)
    for item in items:
        writer.writerow(cells(item))
    return buffer.getvalue()
