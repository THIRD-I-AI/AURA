"""
ED25519 artifact signing for the Counterfactual Audit Engine.

Each persisted artifact gets a 64-byte signature over its
canonical-JSON bytes. The signature is base64-encoded and stored as a
sidecar ``<record_hash>.sig`` file.

Key sourcing (resolved at first use, in this priority order):

1. ``AURA_SIGNING_PRIVATE_KEY_HEX`` env (raw 32-byte hex). Direct;
   convenient for development.
2. ``AURA_SIGNING_PRIVATE_KEY_PATH`` env (file path containing PEM-
   encoded private key). Production path.
3. **Auto-generated ephemeral key** (logged once, NOT persisted). This
   path means a service restart invalidates prior signatures, so the
   verifier should treat ephemeral-key signatures as advisory only.

Signature verification uses the corresponding public key, exposed via
``GET /counterfactual/public-key``. Auditors check
``verify(payload, signature, public_key)`` end-to-end without needing
private-key access.

This module is intentionally tiny and fail-soft: signing failures log a
warning and produce no signature; the engine ships unsigned artifacts
in that case rather than failing the whole counterfactual job. The
auditor view surfaces ``signature_status: "unsigned"`` so it's visible
to humans.
"""
from __future__ import annotations

import base64
import logging
import os
from pathlib import Path
from typing import Optional, Tuple

logger = logging.getLogger("aura.counterfactual.signing")


# ── Optional dep ──────────────────────────────────────────────────────

try:
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ed25519
    _CRYPTO_AVAILABLE = True
except ImportError:  # pragma: no cover
    serialization = None  # type: ignore[assignment]
    ed25519 = None  # type: ignore[assignment]
    InvalidSignature = Exception  # type: ignore[misc,assignment]
    _CRYPTO_AVAILABLE = False


def signing_available() -> bool:
    return _CRYPTO_AVAILABLE


# ── Key resolution (cached) ───────────────────────────────────────────

_KEY_PAIR: Optional[Tuple[object, object]] = None
_KEY_SOURCE: str = "uninitialized"


def _resolve_key_pair() -> Optional[Tuple[object, object]]:
    """Return ``(private_key, public_key)``. ``None`` if signing
    unavailable for any reason."""
    global _KEY_PAIR, _KEY_SOURCE
    if not _CRYPTO_AVAILABLE:
        return None
    if _KEY_PAIR is not None:
        return _KEY_PAIR

    hex_env = os.getenv("AURA_SIGNING_PRIVATE_KEY_HEX", "").strip()
    if hex_env:
        try:
            raw = bytes.fromhex(hex_env)
            if len(raw) != 32:
                raise ValueError(f"expected 32 bytes, got {len(raw)}")
            sk = ed25519.Ed25519PrivateKey.from_private_bytes(raw)
            _KEY_PAIR = (sk, sk.public_key())
            _KEY_SOURCE = "env_hex"
            logger.info("ED25519 signing key loaded from env (hex)")
            return _KEY_PAIR
        except Exception as exc:
            logger.warning("AURA_SIGNING_PRIVATE_KEY_HEX invalid (%s); falling through", exc)

    pem_path = os.getenv("AURA_SIGNING_PRIVATE_KEY_PATH", "").strip()
    if pem_path:
        try:
            data = Path(pem_path).read_bytes()
            sk = serialization.load_pem_private_key(data, password=None)
            if not isinstance(sk, ed25519.Ed25519PrivateKey):
                raise TypeError("key at AURA_SIGNING_PRIVATE_KEY_PATH is not Ed25519")
            _KEY_PAIR = (sk, sk.public_key())
            _KEY_SOURCE = "env_pem"
            logger.info("ED25519 signing key loaded from %s", pem_path)
            return _KEY_PAIR
        except Exception as exc:
            logger.warning("AURA_SIGNING_PRIVATE_KEY_PATH unreadable (%s); falling through", exc)

    # Ephemeral fallback. Logged loudly because it changes the
    # security posture (signatures become advisory rather than
    # auditor-grade).
    sk = ed25519.Ed25519PrivateKey.generate()
    _KEY_PAIR = (sk, sk.public_key())
    _KEY_SOURCE = "ephemeral"
    logger.warning(
        "ED25519 signing key auto-generated for this process. Set "
        "AURA_SIGNING_PRIVATE_KEY_HEX or AURA_SIGNING_PRIVATE_KEY_PATH "
        "for stable signatures across restarts."
    )
    return _KEY_PAIR


def signing_key_source() -> str:
    """One of: ``uninitialized``, ``env_hex``, ``env_pem``, ``ephemeral``."""
    _resolve_key_pair()
    return _KEY_SOURCE


# ── Public surface ────────────────────────────────────────────────────

def sign_bytes(payload: bytes) -> Optional[str]:
    """Return base64 ED25519 signature, or ``None`` on any failure."""
    pair = _resolve_key_pair()
    if pair is None:
        return None
    private_key, _ = pair
    try:
        sig = private_key.sign(payload)
        return base64.b64encode(sig).decode("ascii")
    except Exception as exc:
        logger.warning("ED25519 sign failed: %s", exc)
        return None


def verify_bytes(payload: bytes, signature_b64: str) -> bool:
    """Verify ``signature_b64`` against ``payload`` using the *current*
    public key. Returns ``True`` on valid, ``False`` on any mismatch or
    structural error (never raises)."""
    pair = _resolve_key_pair()
    if pair is None:
        return False
    _, public_key = pair
    try:
        sig = base64.b64decode(signature_b64)
        public_key.verify(sig, payload)
        return True
    except (InvalidSignature, ValueError, TypeError):
        return False
    except Exception as exc:  # pragma: no cover
        logger.warning("ED25519 verify error: %s", exc)
        return False


def public_key_pem() -> Optional[str]:
    """Return the PEM-encoded current public key for auditor consumption."""
    pair = _resolve_key_pair()
    if pair is None:
        return None
    _, public_key = pair
    try:
        pem = public_key.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        return pem.decode("ascii")
    except Exception as exc:  # pragma: no cover
        logger.warning("public_key_pem failed: %s", exc)
        return None
