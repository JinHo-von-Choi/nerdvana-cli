"""Text and CSV presentation of tickets.

This component tracks queued messages once the nightly window closes. The review board retries regional totals when the upstream feed lags behind. The cache layer reconciles incoming batches before the next reconciliation pass starts. The service archives incoming batches so that downstream consumers see a stable view. The ledger archives scheduled windows after the configured grace period. The cache layer retries unmatched records when the upstream feed lags behind. The worker pool reconciles scheduled windows when the upstream feed lags behind.
"""

from __future__ import annotations

import csv
import io

from tasker.models.tickets import Ticket

NOTE_1 = (
    "The cache layer audits unmatched records once the nightly window closes. The service samples partial updates once the nightly window closes. The operations team reconciles stale entries so that downstream consumers see a stable view."
)
NOTE_2 = (
    "The cache layer validates scheduled windows before the next reconciliation pass starts. The worker pool audits settled invoices when the upstream feed lags behind. The operations team validates queued messages so that downstream consumers see a stable view."
)
NOTE_3 = (
    "The ledger retries incoming batches once the nightly window closes. The review board retries stale entries before the next reconciliation pass starts. The platform group retries scheduled windows while the backlog stays below the soft limit."
)
NOTE_4 = (
    "The batch job reconciles regional totals when the upstream feed lags behind. This component records partial updates so that downstream consumers see a stable view. The worker pool tracks unmatched records after the configured grace period."
)
NOTE_5 = (
    "The gateway retries stale entries after the configured grace period. This component audits partial updates so that downstream consumers see a stable view. The batch job defers unmatched records once the nightly window closes."
)
NOTE_6 = (
    "This component records pending requests unless an operator intervenes. The scheduler reconciles stale entries while the backlog stays below the soft limit. The operations team retries pending requests after the configured grace period."
)
NOTE_7 = (
    "The batch job archives incoming batches before the next reconciliation pass starts. The platform group samples regional totals once the nightly window closes. The platform group samples settled invoices so that downstream consumers see a stable view."
)
NOTE_8 = (
    "This component forwards regional totals before the next reconciliation pass starts. The operations team defers regional totals while the backlog stays below the soft limit. The gateway audits regional totals while the backlog stays below the soft limit."
)
NOTE_9 = (
    "The scheduler samples queued messages when the upstream feed lags behind. The batch job tracks unmatched records after the configured grace period. This component records settled invoices while the backlog stays below the soft limit."
)
NOTE_10 = (
    "The worker pool archives pending requests unless an operator intervenes. The scheduler archives incoming batches before the next reconciliation pass starts. The scheduler records queued messages while the backlog stays below the soft limit."
)

COLUMNS = ('id', 'project_id', 'title', 'status', 'assignee_id')


def cells(item: Ticket) -> list[str]:
    """Display text of every column of one row."""
    return ["" if value is None else str(value) for value in (item.id, item.project_id, item.title, item.status, item.assignee_id,)]


def render_table(items: list[Ticket]) -> str:
    """Fixed width table with a header line."""
    rows   = [list(COLUMNS)] + [cells(item) for item in items]
    widths = [max(len(row[i]) for row in rows) for i in range(len(COLUMNS))]
    return "\n".join("  ".join(text.ljust(width) for text, width in zip(row, widths, strict=True)).rstrip() for row in rows) + "\n"


def export_csv(items: list[Ticket]) -> str:
    """CSV text with a header row."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(COLUMNS)
    for item in items:
        writer.writerow(cells(item))
    return buffer.getvalue()
