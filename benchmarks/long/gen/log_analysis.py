"""Task long-log-analysis: answer five questions from ten service logs of 1000 lines each.

Author: 최진호
Date:   2026-10-03

The logs use three line formats and several time zones, so the questions cannot be answered
by treating the files alike. The expected answers are taken by parsing the generated text
with a separate analyser, and the checker embeds only digests of them.
"""

from __future__ import annotations

import json
import random
import re
from collections import Counter
from datetime import UTC, datetime, timedelta, timezone

from .common import TaskBuild, digest, fill, rng_for

TASK_ID   = "long-log-analysis"
LINES     = 1000
START_UTC = datetime(2026, 3, 14, 10, 0, 0, tzinfo=UTC)
TAGS      = ["pay", "auth", "db", "cache", "queue", "io", "rpc", "tls", "disk", "cpu", "mq", "dns"]
ERROR_CODES  = [f"E{1000 + n}" for n in (1, 7, 12, 19, 23, 31, 44, 52, 61, 70, 88, 95)]
ERROR_WEIGHT = [32, 23, 13, 8, 7, 6, 5, 4, 3, 3, 2, 2]
WARN_CODES   = [f"W{2000 + n}" for n in (3, 9, 14, 27, 33)]
BURST_CODE   = "E5107"

# name -> (format, utc offset in minutes)
FILES = {
    "auth":      ("A", 120),  "orders":    ("A", 0),    "search":    ("A", 60),   "inventory": ("A", -180), "reports":   ("A", -300),
    "gateway":   ("B", 0),    "notify":    ("B", 0),    "scheduler": ("B", 0),
    "payments":  ("C", -300), "billing":   ("C", 330),
}
BURSTS      = {"billing": 0, "gateway": 40, "payments": 80, "orders": 125}          # seconds after 12:07:10Z
DECOY_BURST = {"search": 11 * 3600 + 30 * 60, "notify": 11 * 3600 + 50 * 60}         # seconds after 00:00Z, only 6 errors
REAL_GAPS   = {"auth": (600, 700), "reports": (820, 760), "scheduler": (500, 900)}   # file -> (line index, gap seconds)
DECOY_GAPS  = {"orders": (700, 540), "inventory": (650, 480), "billing": (800, 480)}
BURST_START = datetime(2026, 3, 14, 12, 7, 10, tzinfo=UTC)
BURST_SIZE  = 40
DECOY_SIZE  = 6

README = """# Service logs

Ten services wrote one log each on 2026-03-14. Every file starts with one `#` header line.

Formats

- `HH:MM:SS L CODE TAG` with the clock in the zone named by the header (`tz=+HH:MM`)
- `EPOCH L CODE TAG` where EPOCH is Unix seconds in UTC
- `[HH:MM:SS] LEVEL CODE TAG` with the clock in the zone named by the header

L is I, W or E (LEVEL is INFO, WARN or ERROR). CODE is `-` on INFO lines. A line with the tag RESTART
records a service restart.

`python3 check.py` compares answer.json with the reference.
"""

CHECK = '''"""Checks answer.json. Exit status 0 means every answer is right."""

import hashlib
import json
import pathlib
import sys

EXPECTED = __EXPECTED__


def digest(value):
    text = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(text.encode()).hexdigest()[:16]


path = pathlib.Path("answer.json")
if not path.is_file():
    print("answer.json is missing")
    sys.exit(1)
try:
    answer = json.loads(path.read_text(encoding="utf-8"))
except ValueError as exc:
    print(f"answer.json is not valid JSON: {exc}")
    sys.exit(1)
wrong = [key for key, expected in EXPECTED.items() if key not in answer or digest(answer[key]) != expected]
extra = sorted(set(answer) - set(EXPECTED))
for key in wrong:
    print(f"{key}: missing or wrong")
for key in extra:
    print(f"{key}: not a requested key")
if wrong or extra:
    sys.exit(1)
print("ok")
'''

PROMPT = (
    "logs/ holds ten service logs from 2026-03-14 (format notes in README.md). Analyse them and write answer.json with these keys: "
    "top_error_codes (the three error codes with the most ERROR lines across all logs, most frequent first), "
    "worst_hour_utc (the UTC hour with the most ERROR lines across all logs, as 'YYYY-MM-DD HH'), "
    "root_cause_log (file name of the log whose error burst began first in UTC; a burst is 10 or more ERROR lines within 60 seconds), "
    "restart_count (total number of lines tagged RESTART in all logs), "
    "long_gaps (an object mapping each log file name that has a gap of more than 600 seconds between two consecutive lines to the UTC time of the "
    "last line before that gap, formatted 'YYYY-MM-DDTHH:MM:SSZ'). Then make `python3 check.py` pass."
)


def _events(rng: random.Random, name: str) -> list[tuple[datetime, str, str, str]]:
    """Time ordered (utc, level, code, tag) events of one log."""
    burst_n = BURST_SIZE if name in BURSTS else 0
    extra   = burst_n or (DECOY_SIZE if name in DECOY_BURST else 0)
    events: list[tuple[datetime, str, str, str]] = []
    now = START_UTC
    gap = REAL_GAPS.get(name) or DECOY_GAPS.get(name)
    for index in range(LINES - extra):
        now += timedelta(seconds=rng.randrange(6, 37))
        if gap and index == gap[0]:
            now += timedelta(seconds=gap[1])
        roll = rng.random()
        if roll < 0.06:
            events.append((now, "E", rng.choices(ERROR_CODES, ERROR_WEIGHT)[0], rng.choice(TAGS)))
        elif roll < 0.15:
            events.append((now, "W", rng.choice(WARN_CODES), rng.choice(TAGS)))
        elif roll < 0.1675:
            events.append((now, "I", "-", "RESTART"))
        else:
            events.append((now, "I", "-", rng.choice(TAGS)))
    if name in BURSTS:
        at = BURST_START + timedelta(seconds=BURSTS[name])
        for _ in range(burst_n):
            at += timedelta(seconds=rng.randrange(1, 4))
            events.append((at, "E", BURST_CODE, rng.choice(TAGS)))
    if name in DECOY_BURST:
        at = datetime(2026, 3, 14, tzinfo=UTC) + timedelta(seconds=DECOY_BURST[name])
        for _ in range(DECOY_SIZE):
            at += timedelta(seconds=rng.randrange(2, 6))
            events.append((at, "E", rng.choice(ERROR_CODES[:4]), rng.choice(TAGS)))
    events.sort(key=lambda e: e[0])
    return events


def _offset(minutes: int) -> str:
    sign = "+" if minutes >= 0 else "-"
    return f"{sign}{abs(minutes) // 60:02d}:{abs(minutes) % 60:02d}"


def _render(name: str, fmt: str, offset: int, events: list[tuple[datetime, str, str, str]]) -> str:
    local = timezone(timedelta(minutes=offset))
    if fmt == "B":
        lines = [f"{int(t.timestamp())} {lv} {code} {tag}" for t, lv, code, tag in events]
        return "\n".join(["# clock=epoch-utc"] + lines) + "\n"
    word = {"I": "INFO", "W": "WARN", "E": "ERROR"}
    header = f"# tz={_offset(offset)} date=2026-03-14"
    lines = []
    for t, lv, code, tag in events:
        clock = t.astimezone(local).strftime("%H:%M:%S")
        lines.append(f"{clock} {lv} {code} {tag}" if fmt == "A" else f"[{clock}] {word[lv]} {code} {tag}")
    return "\n".join([header] + lines) + "\n"


def _parse(text: str) -> list[tuple[datetime, str, str, str]]:
    """Independent reader of a generated log, used only to compute the expected answers."""
    rows = text.splitlines()
    head, body = rows[0], rows[1:]
    level = {"INFO": "I", "WARN": "W", "ERROR": "E"}
    out = []
    if "epoch" in head:
        for row in body:
            ts, lv, code, tag = row.split(" ")
            out.append((datetime.fromtimestamp(int(ts), tz=UTC), lv, code, tag))
        return out
    sign, hh, mm = re.search(r"tz=([+-])(\d\d):(\d\d)", head).groups()  # type: ignore[union-attr]
    shift = timedelta(hours=int(hh), minutes=int(mm)) * (1 if sign == "+" else -1)
    for row in body:
        parts = row.replace("[", "").replace("]", "").split(" ")
        clock, lv, code, tag = parts
        local = datetime.strptime(f"2026-03-14 {clock}", "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
        out.append((local - shift, level.get(lv, lv), code, tag))
    return out


def _answers(texts: dict[str, str]) -> dict[str, object]:
    parsed  = {f"{name}.log": _parse(text) for name, text in texts.items()}
    errors  = [e for events in parsed.values() for e in events if e[1] == "E"]
    codes   = Counter(e[2] for e in errors).most_common()
    hours   = Counter(e[0].strftime("%Y-%m-%d %H") for e in errors).most_common()
    assert all(codes[i][1] - codes[i + 1][1] >= 8 for i in range(3)), codes[:5]
    assert hours[0][1] - hours[1][1] >= 20, hours[:3]
    starts = {}
    for file, events in parsed.items():
        stamps = [e[0] for e in events if e[1] == "E"]
        for i, first in enumerate(stamps):
            if sum(1 for s in stamps[i:] if s - first <= timedelta(seconds=60)) >= 10:
                starts[file] = first
                break
    gaps = {}
    for file, events in parsed.items():
        for before, after in zip(events, events[1:], strict=False):
            if (after[0] - before[0]).total_seconds() > 600:
                gaps[file] = before[0].strftime("%Y-%m-%dT%H:%M:%SZ")
    return {
        "top_error_codes": [c for c, _ in codes[:3]],
        "worst_hour_utc": hours[0][0],
        "root_cause_log": min(starts, key=lambda f: starts[f]),
        "restart_count": sum(1 for events in parsed.values() for e in events if e[3] == "RESTART"),
        "long_gaps": dict(sorted(gaps.items())),
    }


def build() -> TaskBuild:
    rng   = rng_for(TASK_ID)
    texts = {name: _render(name, fmt, off, _events(rng, name)) for name, (fmt, off) in FILES.items()}
    answers = _answers(texts)
    assert set(answers["long_gaps"]) == {f"{n}.log" for n in REAL_GAPS}, answers["long_gaps"]  # type: ignore[arg-type]
    fixture = {f"logs/{name}.log": text for name, text in texts.items()}
    fixture["README.md"] = README
    fixture["check.py"]  = fill(CHECK, expected=repr({key: digest(value) for key, value in answers.items()}))
    return TaskBuild(
        task_id  = TASK_ID,
        task     = {"prompt": PROMPT, "verify": "python3 check.py", "tags": ["python", "analysis", "logs"]},
        fixture  = fixture,
        solution = {"answer.json": json.dumps(answers, indent=2) + "\n"},
        reading  = [f"logs/{name}.log" for name in texts] + ["README.md"],
    )
