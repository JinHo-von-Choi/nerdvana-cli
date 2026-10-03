# Config schema migration: v1 (INI) to v2 (TOML)

Every service has a v1 file configs/<service>.ini. Write the v2 file configs/<service>.toml next to it and leave
the v1 file as it is. A v2 file holds exactly the tables and keys below; nothing else.

## Value conversions

- Booleans: yes, on, true become true; no, off, false become false (any letter case).
- Durations: a number with the unit ms, s, m or h becomes an integer number of milliseconds
  (250ms is 250, 30s is 30000, 10m is 600000, 1h is 3600000). The key name gets the suffix `_ms`.
- Sizes: a number with the unit kb, mb or gb becomes an integer number of bytes (1kb is 1024, 1mb is 1048576,
  1gb is 1073741824). The key name gets the suffix `_bytes`.
- Lists written as comma separated text become arrays of strings, in the same order, each item trimmed.
- Integers stay integers, everything else stays a string. Surrounding double quotes in v1 values are removed.
- Keys that start with `legacy_` are dropped.
- A key whose converted value equals the default listed below is omitted from the v2 file.

## Tables and keys

| v1 | v2 |
|-|-|
| [service] name | [app] name |
| [service] port | [app] port |
| [service] workers | [app] worker_count (omit when 4) |
| [service] timeout | [app] timeout_ms |
| [service] maintenance | [app] maintenance |
| [service] log_level | [logging] level, lower case (omit when "info") |
| [logging] destinations | [logging] destinations (array) |
| [logging] file | [logging] file |
| [logging] rotate_size | [logging] rotate_size_bytes |
| [database] host, port, name, user | [db] host, port, name, user |
| [database] password_file | [db] password_path |
| [database] ssl | [db] tls |
| [database] connect_timeout | [db] connect_timeout_ms |
| [database] pool_min, pool_max | [db.pool] min (omit when 1), max |
| [cache] enabled, backend | [cache] enabled, backend |
| [cache] ttl | [cache] ttl_ms (omit when 600000) |
| [cache] max_size | [cache] max_size_bytes |
| [auth] mode | [security] scheme |
| [auth] token_ttl | [security] token_ttl_ms (omit when 3600000) |
| [auth] allowed_origins | [security] allowed_origins (array) |
| [limits] request_size | [limits] request_size_bytes |
| [limits] burst | [limits] burst |
| [limits] rate (`100/m`) | [limits.rate] count = 100, per = "minute" |
| [features] feature.<name> | [flags] <name> with every dash in the name replaced by an underscore |
| [env] KEY | [environment] KEY (string value) |
| [routes] route.<n> | one [[route]] entry per line, in file order |

The unit letter of a rate is s (second), m (minute) or h (hour); per holds the spelled out word.

## Routes

A v1 route line is `route.<n> = <path> -> <upstream> | <methods> | <timeout>`. The [[route]] entry has the keys id (the integer n),
path, upstream, methods (array of the comma separated methods) and timeout_ms (the timeout as a duration).

## Omitted tables

A table that would be empty is left out of the v2 file: a service without a [cache] section has no [cache] table, and a
service without feature lines has no [flags] table.

`python3 check.py` parses every v2 file and compares it with the reference, table by table.
