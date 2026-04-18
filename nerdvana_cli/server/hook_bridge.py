"""Hook bridge scaffold — Phase G1 placeholder, G2 full implementation.

Reads a JSON payload from stdin and routes it to the appropriate hook handler.
In Phase G1 only the scaffolding exists; real hook dispatch is implemented in
Phase G2.

작성자: 최진호
작성일: 2026-04-18
"""

from __future__ import annotations

import json
import sys
from typing import Any


def read_hook_payload(stream: Any = None) -> dict[str, Any]:
    """Read one JSON object from *stream* (defaults to ``sys.stdin``).

    Returns the parsed dict, or an empty dict on EOF / parse error.
    """
    src  = stream or sys.stdin
    line = src.readline()
    if not line:
        return {}
    try:
        parsed: Any = json.loads(line)
        return dict(parsed)
    except (json.JSONDecodeError, TypeError, ValueError):
        return {}


class HookBridge:
    """Placeholder bridge between the MCP server and nerdvana hooks.

    Phase G2 will replace ``dispatch`` with real routing logic.
    """

    def dispatch(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Stub dispatch — returns an ack without side effects.

        Parameters
        ----------
        payload:
            Raw JSON hook payload from stdin.

        Returns
        -------
        dict with ``{"status": "noop"}`` until Phase G2.
        """
        return {"status": "noop", "phase": "G1-placeholder"}
