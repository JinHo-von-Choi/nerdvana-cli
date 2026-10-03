"""Schedule expressions: five-field cron and ``every <n><unit>`` intervals.

Author: 최진호
Date:   2026-10-03

A cron expression is ``minute hour day-of-month month day-of-week``. Each field takes ``*``, a
number, a range ``a-b``, a step ``*/n``, ``a-b/n`` or ``a/n`` (from a to the field's maximum), and
comma separated lists of these. Day of week is 0 to 7, with both 0 and 7 meaning Sunday. When both
day-of-month and day-of-week are restricted (neither starts with ``*``), a day matches if either
does; otherwise it must match both (the classic cron rule).
Names (``mon``, ``jan``) and ``@daily`` style shortcuts are not accepted.

An interval is ``every 15m``, ``every 2h`` or ``every 1d``; the shortest is one minute.

Times are naive datetimes in the machine's local time, at minute resolution.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta

# Years searched for the next firing before an expression is declared one that never fires
# (leap day needs up to eight years, e.g. 29 February that falls on a Monday).
_SEARCH_DAYS = 366 * 9

_INTERVAL = re.compile(r"every\s+(\d+)\s*([mhd])", re.IGNORECASE)
_UNIT_SECONDS = {"m": 60, "h": 3600, "d": 86400}

# (name, lowest, highest allowed)
_FIELDS = (
    ("minute",       0, 59),
    ("hour",         0, 23),
    ("day of month", 1, 31),
    ("month",        1, 12),
    ("day of week",  0, 7),
)


class ScheduleError(ValueError):
    """A schedule expression or a scheduler file cannot be used."""


def _number(text: str, name: str) -> int:
    if not (text.isascii() and text.isdigit()):
        raise ScheduleError(f"{name}: '{text}' is not a number")
    return int(text)


def _parse_part(part: str, name: str, low: int, high: int) -> range:
    """The values one comma separated item of a field stands for."""
    base, slash, step_text = part.partition("/")
    step = _number(step_text, name) if slash else 1
    if step < 1:
        raise ScheduleError(f"{name}: a step must be at least 1 in '{part}'")
    if base == "*":
        start, end = low, (6 if name == "day of week" else high)
    elif "-" in base:
        first, _, last = base.partition("-")
        start, end = _number(first, name), _number(last, name)
    else:
        start = _number(base, name)
        end   = (6 if name == "day of week" else high) if slash else start
    if not low <= start <= high or not low <= end <= high or start > end:
        raise ScheduleError(f"{name}: '{part}' is outside {low}-{high} or runs backwards")
    return range(start, end + 1, step)


def _parse_field(text: str, name: str, low: int, high: int) -> frozenset[int]:
    values: set[int] = set()
    for part in text.split(","):
        values.update(_parse_part(part, name, low, high))
    if name == "day of week":
        return frozenset(value % 7 for value in values)
    return frozenset(values)


@dataclass(frozen=True)
class CronSchedule:
    """A parsed five-field cron expression."""

    expression: str
    minutes:    frozenset[int]
    hours:      frozenset[int]
    days:       frozenset[int]
    months:     frozenset[int]
    weekdays:   frozenset[int]
    both_days:  bool

    def _day_matches(self, when: datetime) -> bool:
        in_days    = when.day in self.days
        in_weekday = (when.weekday() + 1) % 7 in self.weekdays
        return in_days and in_weekday if self.both_days else in_days or in_weekday

    def next_after(self, when: datetime) -> datetime:
        """The first firing minute strictly after *when*; ScheduleError when none exists within nine years."""
        current = when.replace(second=0, microsecond=0) + timedelta(minutes=1)
        limit   = current + timedelta(days=_SEARCH_DAYS)
        while current <= limit:
            if current.month not in self.months:
                current = (current.replace(day=1, hour=0, minute=0) + timedelta(days=32)).replace(day=1)
            elif not self._day_matches(current):
                current = current.replace(hour=0, minute=0) + timedelta(days=1)
            elif current.hour not in self.hours:
                current = current.replace(minute=0) + timedelta(hours=1)
            elif current.minute not in self.minutes:
                current += timedelta(minutes=1)
            else:
                return current
        raise ScheduleError(f"'{self.expression}' never fires")

    def due(self, since: datetime, now: datetime, catch_up: timedelta) -> bool:
        """True when a firing minute lies in ``(since, now]``; a window older than *catch_up* is cut to it."""
        return self.next_after(max(since, now - catch_up)) <= now

    def rearm(self, since: datetime, now: datetime, fired: bool) -> datetime:
        """The start of the next window: every checked moment is behind us, fired or not."""
        return now


@dataclass(frozen=True)
class IntervalSchedule:
    """A fixed interval counted from the last start."""

    expression: str
    seconds:    int

    def next_after(self, when: datetime) -> datetime:
        """*when* plus the interval."""
        return when + timedelta(seconds=self.seconds)

    def due(self, since: datetime, now: datetime, catch_up: timedelta) -> bool:
        """True once the interval has passed since *since*."""
        return self.next_after(since) <= now

    def rearm(self, since: datetime, now: datetime, fired: bool) -> datetime:
        """The interval restarts at a firing and is untouched otherwise."""
        return now if fired else since


Schedule = CronSchedule | IntervalSchedule


def parse_schedule(text: str) -> Schedule:
    """Parse a cron expression or an ``every <n><unit>`` interval; ScheduleError says what is wrong."""
    stripped = text.strip()
    if not stripped:
        raise ScheduleError("the schedule is empty")
    interval = _INTERVAL.fullmatch(stripped)
    if interval:
        amount = int(interval.group(1))
        if amount < 1:
            raise ScheduleError("an interval must be at least one minute")
        return IntervalSchedule(stripped, amount * _UNIT_SECONDS[interval.group(2).lower()])
    fields = stripped.split()
    if len(fields) != len(_FIELDS):
        raise ScheduleError(f"expected 5 cron fields or 'every <n>m|h|d', got '{stripped}'")
    minutes, hours, days, months, weekdays = (
        _parse_field(field, name, low, high) for field, (name, low, high) in zip(fields, _FIELDS, strict=True)
    )
    schedule = CronSchedule(
        stripped, minutes, hours, days, months, weekdays,
        both_days=fields[2].startswith("*") or fields[4].startswith("*"),
    )
    schedule.next_after(datetime(2000, 1, 1))
    return schedule
