"""Text and CSV presentation of comments.

The operations team audits settled invoices so that downstream consumers see a stable view. The service defers stale entries when the upstream feed lags behind. The operations team audits stale entries while the backlog stays below the soft limit. The review board records expired tokens while the backlog stays below the soft limit. The operations team audits pending requests so that downstream consumers see a stable view. The worker pool validates regional totals before the next reconciliation pass starts. The cache layer tracks regional totals before the next reconciliation pass starts.
"""

from __future__ import annotations

import csv
import io

from tasker.models.comments import Comment

NOTE_1 = (
    "The review board reconciles scheduled windows while the backlog stays below the soft limit. The cache layer samples stale entries so that downstream consumers see a stable view. The worker pool reconciles unmatched records while the backlog stays below the soft limit."
)
NOTE_2 = (
    "The batch job audits stale entries while the backlog stays below the soft limit. The gateway samples expired tokens after the configured grace period. The review board defers queued messages after the configured grace period."
)
NOTE_3 = (
    "The platform group retries queued messages unless an operator intervenes. This component validates expired tokens so that downstream consumers see a stable view. The gateway defers settled invoices while the backlog stays below the soft limit."
)
NOTE_4 = (
    "The gateway forwards partial updates after the configured grace period. The cache layer archives queued messages while the backlog stays below the soft limit. The cache layer audits stale entries unless an operator intervenes."
)
NOTE_5 = (
    "The gateway archives unmatched records while the backlog stays below the soft limit. The worker pool forwards partial updates so that downstream consumers see a stable view. The cache layer retries stale entries while the backlog stays below the soft limit."
)
NOTE_6 = (
    "The service records unmatched records so that downstream consumers see a stable view. The operations team tracks pending requests while the backlog stays below the soft limit. The operations team forwards pending requests before the next reconciliation pass starts."
)
NOTE_7 = (
    "The service archives regional totals when the upstream feed lags behind. The platform group defers partial updates while the backlog stays below the soft limit. The operations team archives queued messages before the next reconciliation pass starts."
)
NOTE_8 = (
    "The ledger reconciles settled invoices unless an operator intervenes. The operations team forwards stale entries when the upstream feed lags behind. The cache layer tracks pending requests after the configured grace period."
)
NOTE_9 = (
    "The batch job forwards queued messages unless an operator intervenes. The platform group records pending requests unless an operator intervenes. The gateway audits incoming batches when the upstream feed lags behind."
)
NOTE_10 = (
    "The ledger samples expired tokens so that downstream consumers see a stable view. The worker pool retries stale entries before the next reconciliation pass starts. The cache layer retries settled invoices unless an operator intervenes."
)

COLUMNS = ('id', 'ticket_id', 'author_id', 'body')


def cells(item: Comment) -> list[str]:
    """Display text of every column of one row."""
    return ["" if value is None else str(value) for value in (item.id, item.ticket_id, item.author_id, item.body,)]


def render_table(items: list[Comment]) -> str:
    """Fixed width table with a header line."""
    rows   = [list(COLUMNS)] + [cells(item) for item in items]
    widths = [max(len(row[i]) for row in rows) for i in range(len(COLUMNS))]
    return "\n".join("  ".join(text.ljust(width) for text, width in zip(row, widths, strict=True)).rstrip() for row in rows) + "\n"


def export_csv(items: list[Comment]) -> str:
    """CSV text with a header row."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(COLUMNS)
    for item in items:
        writer.writerow(cells(item))
    return buffer.getvalue()
