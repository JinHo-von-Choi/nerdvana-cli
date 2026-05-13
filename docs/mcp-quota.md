# MCP Per-Tenant Quota Enforcement

작성자: 최진호  
작성일: 2026-05-13

## Overview

Per-tenant rate-limit and token quota enforcement sits between the ACL check and
tool dispatch. When no quota file is present the system applies an unconstrained
policy — existing behavior is preserved.

## Configuration file

Place `mcp_quota.yml` in the same directory as `mcp_acl.yml`:

```
~/.nerdvana/mcp_quota.yml
```

## YAML schema

```yaml
# ~/.nerdvana/mcp_quota.yml

default:
  rpm: 60              # requests per rolling 60 s  (0 = unlimited)
  rph: 1000            # requests per rolling 3600 s
  daily_tokens: 500000 # input+output tokens per rolling 24 h
  max_concurrent: 4    # simultaneous in-flight tool calls

tenants:
  alice:
    rpm: 120           # per-tenant overrides beat the default
  noisy-bot:
    rpm: 10
    max_concurrent: 1

roles:
  admin:
    rpm: 0             # 0 = unlimited for admins
```

Resolution order: per-tenant > role > default.

## Loading in code

```python
from pathlib import Path
from nerdvana_cli.server.quota import QuotaPolicyResolver, QuotaStore
from nerdvana_cli.server.mcp_server import NerdvanaMcpServer

resolver = QuotaPolicyResolver.from_yaml(Path.home() / ".nerdvana" / "mcp_quota.yml")
server   = NerdvanaMcpServer(
    quota_resolver = resolver,
    quota_store    = QuotaStore(),
    ...
)
```

Absent file → `QuotaPolicyResolver()` with no policies → all tenants receive the
unconstrained policy → no enforcement. This is the default when `quota_resolver`
is omitted from `NerdvanaMcpServer(...)`.

## Error responses

### HTTP transport

When a tenant exceeds its quota the server returns:

```
HTTP/1.1 429 Too Many Requests
Retry-After: 42

{"error": "quota_exceeded", "limit": "rpm", "retry_after_seconds": 42}
```

### stdio / JSON-RPC transport

`QuotaExceeded` propagates as a tool error. FastMCP serializes it into a standard
MCP error response. The `reason` field contains the human-readable explanation.

## Audit log

Quota-denied calls are recorded with `decision="denied"` and
`error_class="quota_denied:<limit_name>"` (e.g. `quota_denied:rpm`).

## Known limitation — HTTP 200 instead of 429 (mcp 1.27.0)

The MCP lowlevel server (`mcp.server.lowlevel.server`, line ~583) wraps every
tool-handler invocation in a broad `except Exception` block and converts any
exception to an MCP `isError:true` result:

```python
except Exception as e:
    return self._make_error_result(str(e))
```

`QuotaExceeded` is raised inside the FastMCP tool handler that wraps `_dispatch`,
so it is caught here before it can propagate to the ASGI middleware layer.  HTTP
clients therefore receive:

```
HTTP/1.1 200 OK

{"jsonrpc":"2.0","id":1,"result":{"content":[{"type":"text","text":"<reason>"}],"isError":true}}
```

instead of the intended `429 Too Many Requests`.

**Detection**: Every quota-exceeded event on the HTTP transport emits a
structured log entry at `WARNING` level via the `nerdvana.quota` logger:

```
event=quota_exceeded_swallowed_by_fastmcp tenant=<id> tool=<name>
limit=<rpm|rph|daily_tokens|max_concurrent> retry_after=<seconds>
note=mcp==1.27.0 serialises QuotaExceeded as isError:true/HTTP-200
```

Grep or filter for `quota_exceeded_swallowed_by_fastmcp` in your log aggregator.

**Resolution path**: When `mcp` exposes `raise_exceptions=True` in the
Streamable-HTTP session path, `_QuotaErrorMiddleware` will intercept correctly
without code changes.  Track the upstream issue for that flag; update the
`StreamableHTTPSessionManager` instantiation in `mcp_server.py` when available.

## Concurrency note

The default `QuotaStore` is in-process and uses a threading lock. It is safe for
concurrent async tool calls within a single server process. Multi-process
deployments (multiple uvicorn workers) require a shared store — a Redis adapter
slot is reserved in `QuotaStore` for future use.
