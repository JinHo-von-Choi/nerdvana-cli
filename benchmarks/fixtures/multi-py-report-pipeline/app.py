"""CSV text in, report text out."""

from ledger import summarize
from reader import read_records
from render import render_report


def run(text: str) -> str:
    """The spending report for category,amount CSV text."""
    return render_report(summarize(read_records(text)))
