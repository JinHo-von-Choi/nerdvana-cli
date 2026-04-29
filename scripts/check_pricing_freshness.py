"""Scan pricing.yml for stale provider snapshots.

Exit codes:
  0  all snapshots are within the TTL (or --report-only flag used)
  1  one or more snapshots exceed NERDVANA_PRICING_TTL_DAYS (default 90)
"""

import argparse
import os
import re
import sys
from datetime import date, datetime
from pathlib import Path

REPO_ROOT      = Path(__file__).resolve().parent.parent
PRICING_FILE   = REPO_ROOT / "nerdvana_cli" / "providers" / "pricing.yml"
DEFAULT_TTL    = 90
SNAPSHOT_RE    = re.compile(r"#\s*(\d{4}-\d{2}-\d{2})\s+snapshot")
PROVIDER_RE    = re.compile(r"^([a-z_][a-z0-9_]*):\s*$")


def load_snapshots(path: Path) -> list[tuple[str, date]]:
    """Return list of (provider_name, snapshot_date) pairs."""
    results: list[tuple[str, date]] = []
    current_provider: str | None    = None
    snapshot_seen                   = False

    with path.open(encoding="utf-8") as fh:
        for line in fh:
            provider_match = PROVIDER_RE.match(line)
            if provider_match:
                current_provider = provider_match.group(1)
                snapshot_seen    = False
                continue

            if current_provider and not snapshot_seen:
                snap_match = SNAPSHOT_RE.search(line)
                if snap_match:
                    snap_date     = datetime.strptime(snap_match.group(1), "%Y-%m-%d").date()
                    results.append((current_provider, snap_date))
                    snapshot_seen = True

    return results


def main() -> int:
    parser = argparse.ArgumentParser(description="Check pricing.yml snapshot freshness.")
    parser.add_argument(
        "--report-only",
        action="store_true",
        help="Print stale providers but always exit 0.",
    )
    parser.add_argument(
        "--ttl",
        type=int,
        default=int(os.getenv("NERDVANA_PRICING_TTL_DAYS", DEFAULT_TTL)),
        help=f"Stale threshold in days (default {DEFAULT_TTL}, or NERDVANA_PRICING_TTL_DAYS).",
    )
    args = parser.parse_args()

    if not PRICING_FILE.exists():
        print(f"ERROR: pricing file not found: {PRICING_FILE}", file=sys.stderr)
        return 1

    snapshots = load_snapshots(PRICING_FILE)
    today     = date.today()
    stale: list[tuple[str, date, int]] = []

    for provider, snap_date in snapshots:
        age = (today - snap_date).days
        if age > args.ttl:
            stale.append((provider, snap_date, age))

    if not snapshots:
        print("WARNING: no snapshot comments found in pricing.yml", file=sys.stderr)
        return 0

    print(f"Checked {len(snapshots)} provider snapshot(s).  TTL = {args.ttl} days.")

    if not stale:
        print("All snapshots are fresh.")
        return 0

    print(f"\nStale ({len(stale)} provider(s)):")
    for provider, snap_date, age in sorted(stale, key=lambda x: -x[2]):
        print(f"  {provider:<20} last updated {snap_date}  ({age} days ago)")

    if args.report_only:
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
