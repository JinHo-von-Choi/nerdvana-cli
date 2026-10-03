"""A hand-rolled stdio JSON-RPC server for the client resilience tests; run with ``python raw_server.py MODE``.

It answers the 2025 handshake and ``tools/list``. Modes:

- ``ok``: a tool list with one tool whose description is ``DESCRIPTION_BYTES`` bytes long (default 200 KiB);
- ``oversize``: a ``tools/list`` answer on one line of ``LINE_BYTES`` bytes (default 3 MB), past the limit the
  tests set;
- ``hang``: reads its input and never answers;
- ``writer``: one tool, ``write`` (tries to create a file at ``path``) and one, ``connect`` (tries a TCP connection
  to 127.0.0.1 at ``port``); each answers ``ok`` or ``denied: <error>``.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

import json
import os
import socket
import sys
import time


def _send(message: dict[str, object]) -> None:
    sys.stdout.write(json.dumps(message) + "\n")
    sys.stdout.flush()


def _attempt(params: dict[str, object]) -> str:
    """Do what the ``write`` or ``connect`` tool asks and report whether the system allowed it."""
    arguments = params["arguments"]
    assert isinstance(arguments, dict)
    try:
        if params["name"] == "write":
            with open(arguments["path"], "w", encoding="utf-8") as handle:
                handle.write("data")
        else:
            socket.create_connection(("127.0.0.1", arguments["port"]), timeout=3).close()
    except OSError as exc:
        return f"denied: {exc}"
    return "ok"


def main(mode: str) -> None:
    for line in sys.stdin:
        request = json.loads(line)
        method  = request.get("method")
        if mode == "hang":
            time.sleep(60)
        if method == "server/discover":
            _send({"jsonrpc": "2.0", "id": request["id"], "error": {"code": -32601, "message": "Method not found"}})
        elif method == "initialize":
            _send({"jsonrpc": "2.0", "id": request["id"], "result": {
                "protocolVersion": request["params"]["protocolVersion"],
                "capabilities":    {"tools": {}},
                "serverInfo":      {"name": "raw", "version": "1"},
            }})
        elif method == "tools/list" and mode == "writer":
            tools = [{"name": name, "description": name, "inputSchema": {"type": "object"}} for name in ("write", "connect")]
            _send({"jsonrpc": "2.0", "id": request["id"], "result": {"tools": tools}})
        elif method == "tools/call" and mode == "writer":
            _send({"jsonrpc": "2.0", "id": request["id"], "result": {"content": [{"type": "text", "text": _attempt(request["params"])}]}})
        elif method == "tools/list" and mode == "oversize":
            blob = "x" * int(os.environ.get("LINE_BYTES", 3_000_000))
            sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": {"tools": [], "blob": blob}}) + "\n")
            sys.stdout.flush()
        elif method == "tools/list":
            description = "y" * int(os.environ.get("DESCRIPTION_BYTES", 200 * 1024))
            tool        = {"name": "big", "description": description, "inputSchema": {"type": "object"}}
            _send({"jsonrpc": "2.0", "id": request["id"], "result": {"tools": [tool]}})


if __name__ == "__main__":
    main(sys.argv[1])
