"""Analytics engine for NerdVana CLI: tool call stats and session cost tracking.

Storage: ``~/.nerdvana/analytics.sqlite`` (separate from audit.sqlite); the schema is in ``db``.

Classes:
    PricingTable    (pricing): loads pricing.yml, computes USD cost per call.
    CallOrigin      (writer):  which agent made a provider request.
    AnalyticsWriter (writer):  thread-safe writer; records sessions, requests and tool calls.
    AnalyticsReader (reader):  query helpers for /health and dashboard.
"""

from __future__ import annotations

from nerdvana_cli.core.analytics.pricing import PricingTable
from nerdvana_cli.core.analytics.reader import AnalyticsReader
from nerdvana_cli.core.analytics.writer import AnalyticsWriter, CallOrigin

__all__ = ["AnalyticsReader", "AnalyticsWriter", "CallOrigin", "PricingTable"]
