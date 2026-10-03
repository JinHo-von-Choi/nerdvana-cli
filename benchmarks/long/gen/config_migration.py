"""Task long-config-migration: convert 15 service configs from the v1 INI schema to the v2 TOML schema.

Author: 최진호
Date:   2026-10-03

The v1 files are generated from a structured model of raw strings; the reference conversion applies
the documented rules to the same raw strings and is written out as TOML for the solution overlay.
The checker parses the agent's TOML and compares each top-level table by digest.
"""

from __future__ import annotations

import json
import random
import re
import tomllib
from typing import Any

from .common import TaskBuild, digest, fill, prose, rng_for, sha

TASK_ID  = "long-config-migration"
SERVICES = ["orders-api", "billing-worker", "search-gateway", "auth-proxy", "inventory-sync", "notify-hub", "report-builder", "cart-service",
            "catalog-index", "shipping-bridge", "payments-edge", "audit-trail", "scheduler-core", "media-resizer", "geo-lookup"]
FLAGS    = ["fast-search", "bulk-export", "dark-launch", "audit-hooks", "retry-v2", "shadow-writes", "compact-json", "geo-fence", "async-notify", "strict-tls",
            "legacy-auth", "batch-commit", "trace-sampling", "warm-cache", "soft-delete", "rate-shaping", "canary-routing", "pii-masking", "lazy-load", "gzip-bodies"]
UPSTREAMS = ["orders-svc", "billing-svc", "search-svc", "auth-svc", "stock-svc", "notify-svc", "report-svc", "cart-svc", "media-svc", "geo-svc"]
PATHS     = ["orders", "invoices", "search", "login", "stock", "notify", "reports", "cart", "images", "geo", "health", "export", "import", "status", "events"]
ENV_KEYS  = ["REGION", "CLUSTER", "SENTRY_DSN", "FEATURE_SET", "BUILD_ID", "CACHE_PREFIX", "QUEUE_NAME", "TRACE_RATE", "OWNER_TEAM", "ON_CALL", "RELEASE_TRAIN",
             "LOCALE", "TIMEZONE", "BATCH_LIMIT", "CHUNK_SIZE", "RETRY_BUDGET", "SHARD", "TENANT_MODE", "AUDIT_SINK", "DEPLOY_RING"]

SPEC = """# Config schema migration: v1 (INI) to v2 (TOML)

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
"""

CHECK = '''"""Checks the config migration. Exit status 0 means every file is right."""

import hashlib
import json
import pathlib
import sys
import tomllib

EXPECTED = __EXPECTED__
V1_DIGESTS = __V1__


def digest(value):
    text = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(text.encode()).hexdigest()[:16]


problems = []
for service, tables in EXPECTED.items():
    ini = pathlib.Path("configs") / f"{service}.ini"
    if not ini.is_file() or hashlib.sha256(ini.read_text(encoding="utf-8").encode()).hexdigest()[:16] != V1_DIGESTS[service]:
        problems.append(f"{ini}: the v1 file must stay as it was")
    path = pathlib.Path("configs") / f"{service}.toml"
    if not path.is_file():
        problems.append(f"{path} is missing")
        continue
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        problems.append(f"{path} is not valid TOML: {exc}")
        continue
    for table in sorted(set(tables) | set(data)):
        if table not in data:
            problems.append(f"{path}: table [{table}] is missing")
        elif table not in tables:
            problems.append(f"{path}: table [{table}] must not be there")
        elif digest(data[table]) != tables[table]:
            problems.append(f"{path}: table [{table}] differs from the reference")
for problem in problems[:40]:
    print(problem)
if problems:
    print(f"{len(problems)} problem(s)")
    sys.exit(1)
print("ok")
'''

PROMPT = ("configs/ holds 15 service configurations in the v1 INI format. MIGRATION.md describes the v2 TOML schema. Write configs/<service>.toml for every "
          "configs/<service>.ini following MIGRATION.md exactly, and leave the v1 files untouched. `python3 check.py` must pass.")

UNITS_MS = {"ms": 1, "s": 1000, "m": 60_000, "h": 3_600_000}
UNITS_B  = {"kb": 1024, "mb": 1024 ** 2, "gb": 1024 ** 3}
PER      = {"s": "second", "m": "minute", "h": "hour"}


def _bool(raw: str) -> bool:
    return raw.strip().lower() in {"yes", "on", "true"}


def _ms(raw: str) -> int:
    number, unit = re.fullmatch(r"(\d+)(ms|s|m|h)", raw.strip()).groups()  # type: ignore[union-attr]
    return int(number) * UNITS_MS[unit]


def _bytes(raw: str) -> int:
    number, unit = re.fullmatch(r"(\d+)(kb|mb|gb)", raw.strip()).groups()  # type: ignore[union-attr]
    return int(number) * UNITS_B[unit]


def _list(raw: str) -> list[str]:
    return [item.strip() for item in raw.split(",") if item.strip()]


def _unquote(raw: str) -> str:
    raw = raw.strip()
    return raw[1:-1] if len(raw) >= 2 and raw[0] == raw[-1] == '"' else raw


def _v1_model(rng: random.Random, name: str) -> dict[str, list[tuple[str, str]]]:
    """Raw v1 sections: section name to ordered (key, raw value) pairs."""
    sec: dict[str, list[tuple[str, str]]] = {}
    unit = lambda: rng.choice(["ms", "s", "m", "h"])  # noqa: E731
    sec["service"] = [("name", name), ("port", str(rng.randrange(8000, 9000))), ("workers", str(rng.choice([2, 4, 4, 8, 16]))),
                      ("log_level", rng.choice(["INFO", "info", "DEBUG", "WARN", "Error"])), ("timeout", f"{rng.randrange(2, 90)}s"),
                      ("maintenance", rng.choice(["no", "yes", "off", "On"])), ("legacy_banner", f'"{prose(rng, 1)}"')]
    sec["database"] = [("host", f"db-{rng.randrange(1, 9)}.internal"), ("port", str(rng.choice([5432, 5433, 3306]))), ("name", name.replace("-", "_")),
                       ("user", f"svc_{name.split('-')[0]}"), ("password_file", f"/etc/secrets/{name}.pw"),
                       ("pool_min", str(rng.choice([1, 1, 2, 4]))), ("pool_max", str(rng.randrange(8, 60))), ("ssl", rng.choice(["yes", "no", "true", "false"])),
                       ("connect_timeout", f"{rng.randrange(1, 20)}s"), ("legacy_driver", "psycopg2")]
    if rng.random() < 0.8:
        sec["cache"] = [("enabled", rng.choice(["yes", "no", "on"])), ("backend", rng.choice(["redis", "memcached", "local"])),
                        ("ttl", rng.choice(["10m", "10m", "30m", "90s", "2h"])), ("max_size", f"{rng.choice([16, 64, 128, 512])}mb")]
    sec["auth"] = [("mode", rng.choice(["token", "basic", "none", "mtls"])), ("token_ttl", rng.choice(["1h", "1h", "15m", "12h", "45m"])),
                   ("allowed_origins", ", ".join(f"{w}.example.com" for w in rng.sample(["app", "admin", "api", "beta", "docs", "static"], rng.randrange(1, 5))))]
    sec["limits"] = [("request_size", f"{rng.choice([256, 512, 1024, 2048])}kb"), ("rate", f"{rng.randrange(10, 500)}/{rng.choice('smh')}"), ("burst", str(rng.randrange(5, 80)))]
    flags = rng.sample(FLAGS, rng.randrange(0, 20))
    if flags:
        sec["features"] = [(f"feature.{f}", rng.choice(["on", "off", "yes", "no", "true", "false"])) for f in flags]
    sec["logging"] = [("destinations", ", ".join(rng.sample(["stdout", "file", "syslog", "journald"], rng.randrange(1, 4)))), ("file", f"/var/log/{name}.log"),
                      ("rotate_size", f"{rng.choice([10, 50, 100, 250])}mb")]
    sec["env"] = [(k, rng.choice([f'"{rng.randrange(1, 99)}"', f"v{rng.randrange(1, 9)}", f'"{rng.choice(["blue", "green", "emea"])}-{rng.randrange(1, 9)}"']))
                  for k in rng.sample(ENV_KEYS, rng.randrange(8, 20))]
    sec["routes"] = [(f"route.{n}", f"/{rng.choice(PATHS)}/{rng.choice(PATHS)} -> {rng.choice(UPSTREAMS)} | {','.join(rng.sample(['GET', 'POST', 'PUT', 'DELETE', 'PATCH'], rng.randrange(1, 4)))} | {rng.randrange(1, 60)}{unit()}")
                     for n in range(1, rng.randrange(55, 85))]
    return sec


def _ini(rng: random.Random, sec: dict[str, list[tuple[str, str]]]) -> str:
    out = [f"; v1 service configuration\n; {prose(rng, 6)}\n"]
    for name, pairs in sec.items():
        out.append(f"[{name}]")
        out.append(f"; {prose(rng, 8)}")
        for k, (key, value) in enumerate(pairs):
            if k and k % 4 == 0:
                out.append(f"# {prose(rng, 2)}")
            out.append(f"{key} = {value}")
        out.append("")
    return "\n".join(out)


def _convert(sec: dict[str, list[tuple[str, str]]]) -> dict[str, Any]:
    """The reference conversion of the documented rules."""
    raw = {s: dict(pairs) for s, pairs in sec.items()}
    v2: dict[str, Any] = {}
    s = raw["service"]
    app: dict[str, Any] = {"name": _unquote(s["name"]), "port": int(s["port"])}
    if int(s["workers"]) != 4:
        app["worker_count"] = int(s["workers"])
    app.update({"timeout_ms": _ms(s["timeout"]), "maintenance": _bool(s["maintenance"])})
    v2["app"] = app
    lg = raw["logging"]
    logging: dict[str, Any] = {}
    if s["log_level"].lower() != "info":
        logging["level"] = s["log_level"].lower()
    logging.update({"destinations": _list(lg["destinations"]), "file": lg["file"], "rotate_size_bytes": _bytes(lg["rotate_size"])})
    v2["logging"] = logging
    d = raw["database"]
    pool: dict[str, Any] = {} if int(d["pool_min"]) == 1 else {"min": int(d["pool_min"])}
    pool["max"] = int(d["pool_max"])
    v2["db"] = {"host": d["host"], "port": int(d["port"]), "name": d["name"], "user": d["user"], "password_path": d["password_file"],
                "tls": _bool(d["ssl"]), "connect_timeout_ms": _ms(d["connect_timeout"]), "pool": pool}
    if "cache" in raw:
        c = raw["cache"]
        cache: dict[str, Any] = {"enabled": _bool(c["enabled"]), "backend": c["backend"]}
        if _ms(c["ttl"]) != 600_000:
            cache["ttl_ms"] = _ms(c["ttl"])
        cache["max_size_bytes"] = _bytes(c["max_size"])
        v2["cache"] = cache
    a = raw["auth"]
    security: dict[str, Any] = {"scheme": a["mode"]}
    if _ms(a["token_ttl"]) != 3_600_000:
        security["token_ttl_ms"] = _ms(a["token_ttl"])
    security["allowed_origins"] = _list(a["allowed_origins"])
    v2["security"] = security
    lim = raw["limits"]
    count, per = lim["rate"].split("/")
    v2["limits"] = {"request_size_bytes": _bytes(lim["request_size"]), "burst": int(lim["burst"]), "rate": {"count": int(count), "per": PER[per]}}
    if "features" in raw:
        v2["flags"] = {key.removeprefix("feature.").replace("-", "_"): _bool(value) for key, value in sec["features"]}
    v2["environment"] = {key: _unquote(value) for key, value in sec["env"]}
    routes = []
    for key, value in sec["routes"]:
        path, rest = value.split(" -> ")
        upstream, methods, timeout = (part.strip() for part in rest.split("|"))
        routes.append({"id": int(key.split(".")[1]), "path": path, "upstream": upstream, "methods": _list(methods), "timeout_ms": _ms(timeout)})
    v2["route"] = routes
    return v2


def _key(name: str) -> str:
    return name if re.fullmatch(r"[A-Za-z0-9_-]+", name) else json.dumps(name)


def _scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, list):
        return "[" + ", ".join(_scalar(v) for v in value) + "]"
    return str(value) if isinstance(value, int) else json.dumps(value)


def _table(path: str, table: dict[str, Any], out: list[str], array: bool = False) -> None:
    out.append(f"[[{path}]]" if array else f"[{path}]")
    out += [f"{_key(k)} = {_scalar(v)}" for k, v in table.items() if not isinstance(v, dict)]
    out.append("")
    for key, value in table.items():
        if isinstance(value, dict):
            _table(f"{path}.{_key(key)}", value, out)


def _toml(v2: dict[str, Any]) -> str:
    out: list[str] = []
    for name, value in v2.items():
        for entry in (value if isinstance(value, list) else [value]):
            _table(name, entry, out, array=isinstance(value, list))
    return "\n".join(out)


def build() -> TaskBuild:
    rng      = rng_for(TASK_ID)
    fixture  = {"MIGRATION.md": SPEC}
    solution = {}
    expected, v1_digests = {}, {}
    for name in SERVICES:
        model = _v1_model(rng, name)
        text  = _ini(rng, model)
        v2    = _convert(model)
        toml  = _toml(v2)
        assert tomllib.loads(toml) == json.loads(json.dumps(v2)), name
        fixture[f"configs/{name}.ini"]  = text
        solution[f"configs/{name}.toml"] = toml
        expected[name]   = {table: digest(value) for table, value in tomllib.loads(toml).items()}
        v1_digests[name] = sha(text)[:16]
    fixture["check.py"] = fill(CHECK, expected=repr(expected), v1=repr(v1_digests))
    return TaskBuild(
        task_id  = TASK_ID,
        task     = {"prompt": PROMPT, "verify": "python3 check.py", "tags": ["python", "migration", "config"]},
        fixture  = fixture,
        solution = solution,
        reading  = sorted(p for p in fixture if p.startswith("configs/") or p == "MIGRATION.md"),
    )
