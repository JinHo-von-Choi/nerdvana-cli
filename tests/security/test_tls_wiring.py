"""TLS wiring for the MCP HTTP transport.

The server accepts certificate options.  These tests pin the guarantee that
the options actually reach the listening socket, and that startup aborts
whenever they cannot, so bearer tokens are never handed to a plaintext
listener while the operator believes the channel is encrypted.

작성자: 최진호
작성일: 2026-09-10
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime
import ipaddress
import socket
import ssl
import threading
from pathlib import Path
from typing import Any

import pytest

from nerdvana_cli.server.audit import AuditLogger
from nerdvana_cli.server.mcp_server import NerdvanaMcpServer, TlsConfigurationError

pytestmark = pytest.mark.security


# ---------------------------------------------------------------------------
# Certificate material
# ---------------------------------------------------------------------------


class _TlsMaterial:
    """Paths to a self-signed certificate usable both as leaf and as CA."""

    def __init__(self, cert: Path, key: Path, bundle: Path) -> None:
        self.cert   = cert
        self.key    = key
        self.bundle = bundle


@pytest.fixture(scope="session")
def tls_material(tmp_path_factory: pytest.TempPathFactory) -> _TlsMaterial:
    """Generate one self-signed certificate for the whole session.

    Three artefacts are produced: the certificate alone, the key alone, and a
    PEM bundle holding both.  The certificate is marked as a CA so the same
    file doubles as the trust anchor for client verification.
    """
    cryptography = pytest.importorskip("cryptography")
    assert cryptography is not None

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key  = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now  = datetime.datetime.now(datetime.UTC)

    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName([
                x509.DNSName("localhost"),
                x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
            ]),
            critical=False,
        )
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )

    cert_pem = certificate.public_bytes(serialization.Encoding.PEM)
    key_pem  = key.private_bytes(
        encoding             = serialization.Encoding.PEM,
        format               = serialization.PrivateFormat.PKCS8,
        encryption_algorithm = serialization.NoEncryption(),
    )

    base        = tmp_path_factory.mktemp("tls")
    cert_path   = base / "server.crt"
    key_path    = base / "server.key"
    bundle_path = base / "server.pem"
    cert_path.write_bytes(cert_pem)
    key_path.write_bytes(key_pem)
    bundle_path.write_bytes(cert_pem + key_pem)

    return _TlsMaterial(cert=cert_path, key=key_path, bundle=bundle_path)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _free_port() -> int:
    """Bind to port 0 and return the OS-assigned port number."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        port: int = sock.getsockname()[1]
        return port


def _make_server(tmp_path: Path, **kwargs: Any) -> NerdvanaMcpServer:
    """Build a server with an isolated audit database."""
    return NerdvanaMcpServer(
        transport    = "http",
        host         = "127.0.0.1",
        port         = _free_port(),
        audit_logger = AuditLogger(db_path=tmp_path / "audit.sqlite"),
        project_path = tmp_path,
        **kwargs,
    )


class _ConfigSpy:
    """Records the keyword arguments handed to ``uvicorn.Config``."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    @property
    def last(self) -> dict[str, Any]:
        assert self.calls, "uvicorn.Config was never constructed"
        return self.calls[-1]


@pytest.fixture
def config_spy(monkeypatch: pytest.MonkeyPatch) -> _ConfigSpy:
    """Intercept uvicorn so the listener is inspected instead of started."""
    uvicorn = pytest.importorskip("uvicorn")
    spy     = _ConfigSpy()

    class _FakeConfig:
        def __init__(self, **kwargs: Any) -> None:
            spy.calls.append(kwargs)

    class _FakeServer:
        def __init__(self, config: Any) -> None:
            self.config = config

        async def serve(self) -> None:
            return None

    monkeypatch.setattr(uvicorn, "Config", _FakeConfig)
    monkeypatch.setattr(uvicorn, "Server", _FakeServer)
    return spy


# ---------------------------------------------------------------------------
# Wiring
# ---------------------------------------------------------------------------


async def test_tls_options_reach_the_listener(
    tmp_path:     Path,
    tls_material: _TlsMaterial,
    config_spy:   _ConfigSpy,
) -> None:
    """Certificate, key and CA are handed to uvicorn with their real values."""
    server = _make_server(
        tmp_path,
        tls_cert = tls_material.cert,
        tls_key  = tls_material.key,
        tls_ca   = tls_material.cert,
    )

    await server.run()

    kwargs = config_spy.last
    assert kwargs["ssl_certfile"]  == str(tls_material.cert)
    assert kwargs["ssl_keyfile"]   == str(tls_material.key)
    assert kwargs["ssl_ca_certs"]  == str(tls_material.cert)
    assert kwargs["ssl_cert_reqs"] == ssl.CERT_REQUIRED


async def test_ca_turns_on_client_certificate_verification(
    tmp_path:     Path,
    tls_material: _TlsMaterial,
    config_spy:   _ConfigSpy,
) -> None:
    """A CA without CERT_REQUIRED would admit peers presenting no certificate."""
    server = _make_server(
        tmp_path,
        tls_cert = tls_material.bundle,
        tls_ca   = tls_material.cert,
    )

    await server.run()

    assert config_spy.last["ssl_cert_reqs"] == ssl.CERT_REQUIRED


async def test_bundle_needs_no_separate_key(
    tmp_path:     Path,
    tls_material: _TlsMaterial,
    config_spy:   _ConfigSpy,
) -> None:
    """A PEM holding certificate and key is accepted on its own."""
    server = _make_server(tmp_path, tls_cert=tls_material.bundle)

    await server.run()

    kwargs = config_spy.last
    assert kwargs["ssl_certfile"] == str(tls_material.bundle)
    assert kwargs["ssl_keyfile"] is None


async def test_plaintext_listener_without_tls_options(
    tmp_path:   Path,
    config_spy: _ConfigSpy,
) -> None:
    """The existing plaintext path is untouched when no option is supplied."""
    server = _make_server(tmp_path)

    await server.run()

    kwargs = config_spy.last
    assert kwargs["ssl_certfile"]  is None
    assert kwargs["ssl_keyfile"]   is None
    assert kwargs["ssl_ca_certs"]  is None
    assert kwargs["ssl_cert_reqs"] == ssl.CERT_NONE


# ---------------------------------------------------------------------------
# Refusals: no silent fallback to plaintext
# ---------------------------------------------------------------------------


def test_missing_certificate_refuses_startup(
    tmp_path:   Path,
    config_spy: _ConfigSpy,
) -> None:
    """A certificate path that does not exist aborts instead of serving clear."""
    with pytest.raises(TlsConfigurationError) as excinfo:
        _make_server(tmp_path, tls_cert=tmp_path / "absent.crt")

    assert "--tls-cert" in str(excinfo.value)
    assert config_spy.calls == []


def _can_read(path: Path) -> bool:
    try:
        with path.open("rb"):
            return True
    except OSError:
        return False


def test_unreadable_certificate_refuses_startup(
    tmp_path:     Path,
    tls_material: _TlsMaterial,
    config_spy:   _ConfigSpy,
) -> None:
    """A certificate the process cannot open aborts instead of serving clear."""
    blocked = tmp_path / "blocked.pem"
    blocked.write_bytes(tls_material.bundle.read_bytes())
    blocked.chmod(0o000)
    if _can_read(blocked):
        pytest.skip("running with privileges that bypass file permissions")

    with pytest.raises(TlsConfigurationError):
        _make_server(tmp_path, tls_cert=blocked)

    assert config_spy.calls == []


def test_certificate_without_key_refuses_startup(
    tmp_path:     Path,
    tls_material: _TlsMaterial,
    config_spy:   _ConfigSpy,
) -> None:
    """A leaf certificate with no key and no --tls-key cannot serve TLS."""
    with pytest.raises(TlsConfigurationError) as excinfo:
        _make_server(tmp_path, tls_cert=tls_material.cert)

    assert "--tls-key" in str(excinfo.value)
    assert config_spy.calls == []


def test_ca_without_certificate_refuses_startup(
    tmp_path:     Path,
    tls_material: _TlsMaterial,
    config_spy:   _ConfigSpy,
) -> None:
    """mTLS asked for without a server certificate is not silently dropped."""
    with pytest.raises(TlsConfigurationError) as excinfo:
        _make_server(tmp_path, tls_ca=tls_material.cert)

    assert "--tls-cert" in str(excinfo.value)
    assert config_spy.calls == []


def test_key_without_certificate_refuses_startup(
    tmp_path:     Path,
    tls_material: _TlsMaterial,
) -> None:
    """A key on its own cannot configure a listener."""
    with pytest.raises(TlsConfigurationError):
        _make_server(tmp_path, tls_key=tls_material.key)


def test_tls_options_on_stdio_refuse_startup(
    tmp_path:     Path,
    tls_material: _TlsMaterial,
) -> None:
    """stdio has no socket to encrypt, so the options are rejected, not ignored."""
    with pytest.raises(TlsConfigurationError) as excinfo:
        NerdvanaMcpServer(
            transport    = "stdio",
            tls_cert     = tls_material.bundle,
            audit_logger = AuditLogger(db_path=tmp_path / "audit.sqlite"),
            project_path = tmp_path,
        )

    assert "stdio" in str(excinfo.value)


# ---------------------------------------------------------------------------
# End to end: a real handshake against the running listener
# ---------------------------------------------------------------------------


@pytest.mark.slow
def test_listener_completes_a_tls_handshake(
    tmp_path:     Path,
    tls_material: _TlsMaterial,
) -> None:
    """The started socket negotiates TLS with the configured certificate."""
    pytest.importorskip("uvicorn")

    port   = _free_port()
    audit  = AuditLogger(db_path=tmp_path / "audit.sqlite")
    server = NerdvanaMcpServer(
        transport    = "http",
        host         = "127.0.0.1",
        port         = port,
        tls_cert     = tls_material.bundle,
        audit_logger = audit,
        project_path = tmp_path,
    )

    loop_holder: list[asyncio.AbstractEventLoop] = []

    def _run() -> None:
        loop = asyncio.new_event_loop()
        loop_holder.append(loop)
        try:
            # Teardown cancels the serve task; the resulting CancelledError is
            # the expected way this thread ends.
            with contextlib.suppress(Exception, asyncio.CancelledError):
                loop.run_until_complete(server.run())
        finally:
            loop.close()

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()

    context                = ssl.create_default_context(cafile=str(tls_material.cert))
    context.check_hostname = True
    peer_certificate: dict[str, Any] | None = None
    negotiated:       str  | None           = None

    try:
        for _attempt in range(100):
            try:
                with (
                    socket.create_connection(("127.0.0.1", port), timeout=2) as raw,
                    context.wrap_socket(raw, server_hostname="localhost") as tls,
                ):
                    peer_certificate = tls.getpeercert()
                    negotiated       = tls.version()
                break
            except ssl.SSLError:
                raise
            except OSError:
                threading.Event().wait(0.1)
        else:
            pytest.fail(f"TLS listener never accepted a connection on port {port}")
    finally:
        for loop in loop_holder:
            for task in asyncio.all_tasks(loop):
                loop.call_soon_threadsafe(task.cancel)
        thread.join(timeout=5)

    assert negotiated is not None and negotiated.startswith("TLS")
    assert peer_certificate is not None
    common_names = {
        value
        for rdn in peer_certificate.get("subject", ())
        for key, value in rdn
        if key == "commonName"
    }
    assert "localhost" in common_names
