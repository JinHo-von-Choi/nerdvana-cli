# Service logs

Ten services wrote one log each on 2026-03-14. Every file starts with one `#` header line.

Formats

- `HH:MM:SS L CODE TAG` with the clock in the zone named by the header (`tz=+HH:MM`)
- `EPOCH L CODE TAG` where EPOCH is Unix seconds in UTC
- `[HH:MM:SS] LEVEL CODE TAG` with the clock in the zone named by the header

L is I, W or E (LEVEL is INFO, WARN or ERROR). CODE is `-` on INFO lines. A line with the tag RESTART
records a service restart.

`python3 check.py` compares answer.json with the reference.
