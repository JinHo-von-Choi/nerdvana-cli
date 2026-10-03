"""TLS validation for the HTTP transport of the NerdVana MCP server.

Turns the certificate options of ``nerdvana serve`` into the ``ssl_*`` parameters of uvicorn and
refuses to start when the material cannot be used.

작성자: 최진호
작성일: 2026-10-03
"""

from __future__ import annotations

import ssl
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class TlsConfigurationError(RuntimeError):
    """Raised when TLS material is supplied but cannot be wired to the socket.

    Startup aborts instead of degrading to plaintext: an operator who passes
    certificate options believes the transport is encrypted, and a silently
    ignored option would put bearer tokens on the wire in the clear.
    """


@dataclass(frozen=True)
class TlsSettings:
    """The uvicorn ``ssl_*`` parameters; the defaults describe a plaintext listener."""

    certfile:  str | None = None
    keyfile:   str | None = None
    ca_certs:  str | None = None
    cert_reqs: int        = ssl.CERT_NONE

    def uvicorn_options(self) -> dict[str, Any]:
        """Keyword arguments for ``uvicorn.Config``."""
        return {
            "ssl_certfile":  self.certfile,
            "ssl_keyfile":   self.keyfile,
            "ssl_ca_certs":  self.ca_certs,
            "ssl_cert_reqs": self.cert_reqs,
        }


def resolve_tls(
    transport: str,
    cert:      Path | None,
    key:       Path | None,
    ca:        Path | None,
) -> TlsSettings:
    """Validate the TLS inputs and derive the uvicorn ssl_* parameters.

    Every failure path raises :class:`TlsConfigurationError`.  There is
    deliberately no branch that keeps the supplied material unused and
    falls back to a plaintext listener.
    """
    supplied: dict[str, Path | None] = {"--tls-cert": cert, "--tls-key": key, "--tls-ca": ca}
    given = {flag: path for flag, path in supplied.items() if path is not None}
    if not given:
        return TlsSettings()

    flags = ", ".join(sorted(given))

    if transport != "http":
        raise TlsConfigurationError(
            f"{flags} supplied but transport is {transport!r}. "
            "TLS terminates a network socket and has no meaning on stdio; "
            "start with --transport http or drop the TLS options."
        )

    if cert is None:
        raise TlsConfigurationError(
            f"{flags} supplied without --tls-cert. "
            "A server certificate is required to serve over TLS."
        )

    for flag, path in given.items():
        _require_readable(flag, path)

    if key is None and not _contains_private_key(cert):
        raise TlsConfigurationError(
            f"certificate {cert} carries no private key and no key "
            "file was supplied. Pass the private key (--tls-key) or point "
            "--tls-cert at a PEM bundle holding both the certificate and "
            "its key."
        )

    keyfile = str(key) if key is not None else None
    if ca is None:
        return TlsSettings(certfile=str(cert), keyfile=keyfile)
    # A CA without CERT_REQUIRED verifies nothing: peers that present no
    # certificate would still be admitted, which is not mTLS.
    return TlsSettings(
        certfile  = str(cert),
        keyfile   = keyfile,
        ca_certs  = str(ca),
        cert_reqs = ssl.CERT_REQUIRED,
    )


def _require_readable(flag: str, path: Path) -> None:
    """Fail fast when TLS material is missing or unreadable."""
    try:
        with path.open("rb"):
            pass
    except OSError as exc:
        raise TlsConfigurationError(
            f"{flag} {path} cannot be read: {exc.strerror or exc}"
        ) from exc


def _contains_private_key(path: Path) -> bool:
    """Report whether a PEM file embeds a private key block."""
    try:
        blob = path.read_bytes()
    except OSError as exc:
        raise TlsConfigurationError(
            f"{path} cannot be read: {exc.strerror or exc}"
        ) from exc
    return b"PRIVATE KEY-----" in blob
