"""BUG-560 / ADR-001: the connection form's "Use SSL" switch was stored and then
dropped, so every Postgres and MySQL connection went out in plaintext. TLS now
reaches the driver, and verifies the certificate as the name the caller entered even
though BUG-547 connects to the pinned IP."""
from __future__ import annotations

import datetime
import os
import socket
import ssl
import tempfile
import threading

import pytest

asyncpg = pytest.importorskip("asyncpg")
pytest.importorskip("cryptography")

from cryptography import x509  # noqa: E402
from cryptography.hazmat.primitives import hashes, serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import ec  # noqa: E402
from cryptography.x509.oid import NameOID  # noqa: E402

from connectors.base import ConnectorConfig, SourceType  # noqa: E402
from shared.connector_policy import ConnectorPolicyError, open_connector  # noqa: E402
from shared.db_tls import TLSConfigError, build_ssl_context, effective_tls_mode  # noqa: E402

DB_NAME = "db.customer.test"


def _self_signed(name: str):
    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder().subject_name(subject).issuer_name(subject).public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=1)).not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(name)]), False)
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), True)
        .sign(key, hashes.SHA256())
    )
    cert_pem = cert.public_bytes(serialization.Encoding.PEM).decode()
    key_pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                serialization.NoEncryption()).decode()
    return cert_pem, key_pem


@pytest.fixture(scope="module")
def server_cert():
    return _self_signed(DB_NAME)


class _FakePostgresTLS:
    """Answers Postgres's SSLRequest with 'S' and completes a real TLS handshake,
    recording whether it succeeded and which SNI name the client sent."""

    def __init__(self, cert_pem: str, key_pem: str):
        self._dir = tempfile.mkdtemp()
        chain = os.path.join(self._dir, "server.pem")
        with open(chain, "w", encoding="utf-8") as fh:
            fh.write(cert_pem + key_pem)
        self.ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self.ctx.load_cert_chain(chain)
        self.sni = None
        self.ctx.sni_callback = lambda sslobj, name, ctx: setattr(self, "sni", name)
        self.handshake_ok = False
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(1)
        self.port = self.sock.getsockname()[1]
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self):
        conn, _ = self.sock.accept()
        try:
            conn.recv(8)  # SSLRequest
            conn.sendall(b"S")
            tls = self.ctx.wrap_socket(conn, server_side=True)
            self.handshake_ok = True
            tls.close()
        except (ssl.SSLError, OSError):
            pass
        finally:
            self.sock.close()


def _cfg(**kw):
    base = dict(source_type=SourceType.POSTGRESQL, name="p", host="127.0.0.1", port=5432,
                username="u", password="p", database="d")
    base.update(kw)
    return ConnectorConfig(**base)


async def _asyncpg_handshake(server, ctx):
    try:
        conn = await asyncpg.connect(host="127.0.0.1", port=server.port, user="u", password="p",
                                     database="d", ssl=ctx, timeout=5)
        await conn.close()
    except Exception as exc:  # the fake server hangs up after the handshake
        return exc
    return None


def test_the_old_switch_maps_to_verify_full_and_off_stays_plaintext():
    assert effective_tls_mode(None, True) == "verify-full"
    assert effective_tls_mode(None, False) == "disable"
    assert effective_tls_mode("REQUIRE", False) == "require"
    with pytest.raises(TLSConfigError):
        effective_tls_mode("prefer", True)
    assert issubclass(TLSConfigError, ConnectorPolicyError)  # a 400 wherever a policy error is


def test_plaintext_stays_plaintext():
    assert build_ssl_context(_cfg(ssl=False)) is None


@pytest.mark.asyncio
async def test_verify_full_through_a_pinned_ip_checks_the_entered_name(server_cert):
    cert_pem, key_pem = server_cert
    server = _FakePostgresTLS(cert_pem, key_pem)
    ctx = build_ssl_context(_cfg(ssl=True, tls_server_name=DB_NAME, tls_ca_pem=cert_pem))
    await _asyncpg_handshake(server, ctx)
    assert server.handshake_ok
    assert server.sni == DB_NAME  # not the IP asyncpg was given


@pytest.mark.asyncio
async def test_verify_full_refuses_a_certificate_for_another_name(server_cert):
    cert_pem, key_pem = server_cert
    server = _FakePostgresTLS(cert_pem, key_pem)
    ctx = build_ssl_context(_cfg(ssl=True, tls_server_name="other.customer.test", tls_ca_pem=cert_pem))
    exc = await _asyncpg_handshake(server, ctx)
    assert isinstance(exc, ssl.SSLCertVerificationError)
    assert not server.handshake_ok


@pytest.mark.asyncio
async def test_verify_full_refuses_an_untrusted_ca(server_cert):
    cert_pem, key_pem = server_cert
    server = _FakePostgresTLS(cert_pem, key_pem)
    ctx = build_ssl_context(_cfg(ssl=True, tls_server_name=DB_NAME))  # no customer CA
    exc = await _asyncpg_handshake(server, ctx)
    assert isinstance(exc, ssl.SSLCertVerificationError)


@pytest.mark.asyncio
async def test_require_encrypts_without_verifying(server_cert):
    cert_pem, key_pem = server_cert
    server = _FakePostgresTLS(cert_pem, key_pem)
    ctx = build_ssl_context(_cfg(tls_mode="require", tls_server_name="anything.test"))
    await _asyncpg_handshake(server, ctx)
    assert server.handshake_ok


def test_a_client_certificate_loads_and_its_key_file_is_removed(server_cert, monkeypatch):
    cert_pem, key_pem = _self_signed("aura-client")
    made = []
    real = tempfile.mkstemp
    monkeypatch.setattr(tempfile, "mkstemp", lambda *a, **k: made.append(real(*a, **k)) or made[-1])
    ctx = build_ssl_context(_cfg(tls_mode="require", tls_client_cert_pem=cert_pem, tls_client_key_pem=key_pem))
    assert ctx is not None
    assert made and not os.path.exists(made[0][1])


def test_a_mismatched_client_pair_or_half_a_pair_is_refused(server_cert):
    cert_pem, _ = _self_signed("aura-client")
    _, other_key = _self_signed("someone-else")
    with pytest.raises(TLSConfigError):
        build_ssl_context(_cfg(tls_mode="require", tls_client_cert_pem=cert_pem, tls_client_key_pem=other_key))
    with pytest.raises(TLSConfigError):
        build_ssl_context(_cfg(tls_mode="require", tls_client_cert_pem=cert_pem))


@pytest.mark.asyncio
async def test_open_connector_keeps_the_entered_name_and_pins_the_address(monkeypatch):
    monkeypatch.delenv("AURA_CONNECTORS_ALLOW_PRIVATE_HOSTS", raising=False)
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 0))])
    built = await open_connector("postgresql", _cfg(host=DB_NAME, ssl=True, tls_server_name="spoofed.test"),
                                 files_root=None, build=lambda c: c)
    assert built.host == "8.8.8.8"
    assert built.tls_server_name == DB_NAME  # from host, not the caller-supplied value


@pytest.mark.asyncio
async def test_a_bad_ca_bundle_is_a_policy_error_at_open(monkeypatch):
    monkeypatch.delenv("AURA_CONNECTORS_ALLOW_PRIVATE_HOSTS", raising=False)
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 0))])
    with pytest.raises(ConnectorPolicyError):
        await open_connector("postgresql", _cfg(host=DB_NAME, ssl=True, tls_ca_pem="not a certificate"),
                             files_root=None, build=lambda c: c)


@pytest.mark.asyncio
@pytest.mark.parametrize("module,attr", [("connectors.postgresql_connector", "asyncpg"),
                                         ("connectors.mysql_connector", "aiomysql")])
async def test_both_connectors_hand_the_context_to_the_driver(monkeypatch, module, attr):
    import importlib

    mod = importlib.import_module(module)
    seen = {}

    async def fake_create_pool(**kw):
        seen.update(kw)
        raise ConnectionError("stop before the network")

    driver = getattr(mod, attr)
    monkeypatch.setattr(driver, "create_pool", fake_create_pool)
    cls = mod.PostgreSQLConnector if attr == "asyncpg" else mod.MySQLConnector
    source = SourceType.POSTGRESQL if attr == "asyncpg" else SourceType.MYSQL
    await cls(_cfg(source_type=source, ssl=True, tls_server_name=DB_NAME)).connect()
    assert isinstance(seen.get("ssl"), ssl.SSLContext)
    seen.clear()
    await cls(_cfg(source_type=source, ssl=False)).connect()
    assert seen.get("ssl") is None


def test_a_saved_connection_carries_its_tls_settings():
    from api_gateway.routers.connections import _stored_connector_config

    cfg = _stored_connector_config(
        {"type": "postgresql", "name": "n", "host": DB_NAME, "port": 5432, "ssl": True},
        "pw", {"tls_mode": "verify-full", "tls_ca_pem": "PEM"},
    )
    assert cfg.ssl is True and cfg.tls_mode == "verify-full" and cfg.tls_ca_pem == "PEM"
    assert cfg.extra_params is None  # TLS keys do not leak into connector extra params
