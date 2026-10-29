"""TLS for connections to caller-named databases (ADR-001, BUG-560).

BUG-547 connects to the IP address that passed the SSRF check, never the name, so a
rebinding DNS answer cannot redirect the driver. Both drivers verify the server
certificate against the host they were given (asyncpg and aiomysql pass
``server_hostname=host`` to ``start_tls``), which would now be the IP and fail for
every real certificate. ``PinnedNameContext`` checks the certificate, and sends SNI,
as the name the customer entered while the socket still goes to the vetted address.
"""
from __future__ import annotations

import os
import ssl
import tempfile
from typing import Any, Optional

from shared.connector_policy import ConnectorPolicyError

TLS_MODES = ("disable", "require", "verify-full")


class TLSConfigError(ConnectorPolicyError):
    """The connection's TLS settings cannot be used (a 400 wherever a policy error is)."""


class PinnedNameContext(ssl.SSLContext):
    """Verifies the peer as ``expected_name`` whatever ``server_hostname`` the driver passes."""

    expected_name: Optional[str] = None

    def wrap_bio(self, incoming, outgoing, server_side=False, server_hostname=None, session=None):
        return super().wrap_bio(incoming, outgoing, server_side, self.expected_name, session)

    def wrap_socket(self, sock, server_side=False, do_handshake_on_connect=True,
                    suppress_ragged_eofs=True, server_hostname=None, session=None):
        return super().wrap_socket(sock, server_side, do_handshake_on_connect,
                                   suppress_ragged_eofs, self.expected_name, session)


def effective_tls_mode(tls_mode: Optional[str], legacy_ssl: Any = False) -> str:
    """The mode a connection runs in: an explicit ``tls_mode``, else the old boolean
    ``ssl`` switch (True meant "use SSL", so it maps to the verifying mode)."""
    if tls_mode:
        mode = str(tls_mode).strip().lower()
        if mode not in TLS_MODES:
            raise TLSConfigError(f"tls_mode must be one of {', '.join(TLS_MODES)}")
        return mode
    return "verify-full" if legacy_ssl is True or str(legacy_ssl).lower() == "true" else "disable"


def _load_client_cert(ctx: ssl.SSLContext, cert_pem: str, key_pem: str) -> None:
    # load_cert_chain reads files only. The key lives on disk 0600 for the length of
    # this call and is removed straight after (security.md: secure memory lifecycle).
    fd, path = tempfile.mkstemp(suffix=".pem")
    try:
        os.chmod(path, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(cert_pem.strip() + "\n" + key_pem.strip() + "\n")
        ctx.load_cert_chain(path)
    except ssl.SSLError as exc:
        raise TLSConfigError("client certificate and key do not form a valid pair") from exc
    finally:
        os.remove(path)


def build_ssl_context(config: Any) -> Optional[ssl.SSLContext]:
    """The ``ssl=`` argument for asyncpg/aiomysql, or None for a plaintext connection."""
    mode = effective_tls_mode(getattr(config, "tls_mode", None), getattr(config, "ssl", False))
    if mode == "disable":
        return None
    if mode == "require":
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    else:
        name = getattr(config, "tls_server_name", None) or getattr(config, "host", None)
        if not name:
            raise TLSConfigError("verify-full needs the database host name")
        ctx = PinnedNameContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.expected_name = str(name)
        ctx.load_default_certs()
        try:
            import certifi
            ctx.load_verify_locations(certifi.where())
        except ImportError:
            pass
        ca_pem = getattr(config, "tls_ca_pem", None)
        if ca_pem:
            try:
                ctx.load_verify_locations(cadata=str(ca_pem))
            except (ssl.SSLError, ValueError) as exc:
                raise TLSConfigError("tls_ca_pem is not a valid PEM certificate bundle") from exc
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    cert_pem = getattr(config, "tls_client_cert_pem", None)
    key_pem = getattr(config, "tls_client_key_pem", None)
    if cert_pem or key_pem:
        if not (cert_pem and key_pem):
            raise TLSConfigError("a client certificate needs both tls_client_cert_pem and tls_client_key_pem")
        _load_client_cert(ctx, str(cert_pem), str(key_pem))
    return ctx
