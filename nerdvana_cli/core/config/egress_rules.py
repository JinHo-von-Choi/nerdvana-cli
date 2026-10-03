"""The grammar of the egress settings: allowed domain patterns and proxy credentials.

Author: 최진호
Date:   2026-10-03

``sandbox.allowed_domains`` and ``secrets.proxy_credentials`` are checked with these when the settings
load, and the egress proxy compares hosts and adds credentials with the same definitions.
"""

from __future__ import annotations

import contextlib
import ipaddress
import re
from collections.abc import Mapping
from dataclasses import dataclass

_PATTERN = re.compile(r"^(?:\*\.)?[a-z0-9_](?:[a-z0-9_.-]*[a-z0-9_])?$")


def normalize_host(host: str) -> str:
    """The host in the form patterns are compared in: lower case, no brackets, no trailing dot."""
    return host.strip().strip("[]").rstrip(".").lower()


def normalize_pattern(entry: str) -> str:
    """Validate one ``allowed_domains`` entry and return it normalized; ValueError when it is not one.

    An entry is a host name, an IP address, or ``*.suffix``. A scheme, a port, a path or any other wildcard is rejected.
    """
    pattern = normalize_host(entry)
    with contextlib.suppress(ValueError):
        return str(ipaddress.ip_address(pattern))
    if not _PATTERN.match(pattern) or pattern.endswith("*."):
        raise ValueError(f"'{entry}' is not a host name, an IP address or *.suffix")
    return pattern


_HEADER_NAME   = re.compile(r"^[A-Za-z0-9-]+$")
_VARIABLE_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass(frozen=True)
class ProxyCredential:
    """A credential the egress proxy adds to requests for one domain pattern.

    The spec of ``secrets.proxy_credentials`` is ``VARIABLE`` (sent as ``Authorization: Bearer <value>``) or
    ``Header-Name:VARIABLE`` (the value is sent as it is, so a Basic or API-key header carries its own prefix).
    """

    domain:   str
    header:   str
    variable: str
    bearer:   bool

    @classmethod
    def parse(cls, domain: str, spec: str) -> ProxyCredential:
        """Read one ``domain: spec`` entry; ValueError when the spec is not well formed."""
        header, colon, variable = spec.strip().rpartition(":")
        if not _VARIABLE_NAME.match(variable):
            raise ValueError(f"'{spec}' must be VARIABLE or Header-Name:VARIABLE with an environment variable name")
        if colon and not _HEADER_NAME.match(header):
            raise ValueError(f"'{header}' is not a header name")
        return cls(domain, header if colon else "Authorization", variable, not colon)

    def header_pair(self, environ: Mapping[str, str]) -> tuple[str, str] | None:
        """The (name, value) to add, or None when the variable is unset, empty or not safe to send in a header."""
        value = environ.get(self.variable, "")
        if not value or any(char in value for char in "\r\n\0"):
            return None
        return self.header, f"Bearer {value}" if self.bearer else value
