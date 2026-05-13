"""NerdVana CLI MCP server package — Phase G1.

Exposes nerdvana tools over MCP 1.0 (stdio + HTTP JSON-RPC) with
API-key authentication, role-based ACL, and SQLite audit logging.

작성자: 최진호
작성일: 2026-04-18
"""

from __future__ import annotations

from nerdvana_cli.server.acl import ACLManager
from nerdvana_cli.server.audit import AuditLogger
from nerdvana_cli.server.auth import AuthManager

__all__ = [
    "ACLManager",
    "AuditLogger",
    "AuthManager",
    "NerdvanaMcpServer",
]


def __getattr__(name: str) -> object:
    if name == "NerdvanaMcpServer":
        from nerdvana_cli.server.mcp_server import NerdvanaMcpServer
        return NerdvanaMcpServer
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
