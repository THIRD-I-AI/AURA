"""Symmetric encryption for stored third-party credentials.

Database connection passwords have to be kept, not hashed: AURA must be able to
reproduce the plaintext to open a connection. So this is encryption at rest,
deliberately distinct from `shared/auth.py`'s password *hashing* (bcrypt, one
way, for AURA's own logins) and from `counterfactual_service/signing.py`'s
ed25519 *signing* (asymmetric, for audit certificates).

Key handling mirrors the fail-closed posture the rest of `shared/config.py`
takes: a production deploy with no configured key refuses to start rather than
quietly protecting secrets with something guessable.
"""
from __future__ import annotations

import base64
import hashlib
import logging
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger("aura.credentials")

_DEV_KEY_SALT = b"aura-credential-encryption-v1"
_fernet: Optional[Fernet] = None


class CredentialEncryptionError(RuntimeError):
    """Raised when a credential cannot be encrypted or decrypted."""


def _derive_dev_key(secret_key: str) -> bytes:
    """Deterministic Fernet key from SECRET_KEY, for non-production only.

    Deriving rather than generating keeps dev restarts from orphaning every
    stored credential. It is NOT used in production because it couples
    credential decryptability to SECRET_KEY: rotating that key — which
    operators are told to do freely, since it otherwise only invalidates
    sessions — would silently render every stored connection password
    unreadable.
    """
    digest = hashlib.sha256(_DEV_KEY_SALT + secret_key.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


def _get_fernet() -> Fernet:
    global _fernet
    if _fernet is not None:
        return _fernet

    from shared.config import settings

    configured = (getattr(settings, "credential_encryption_key", "") or "").strip()
    if configured:
        try:
            _fernet = Fernet(configured.encode("utf-8"))
            return _fernet
        except (ValueError, TypeError) as exc:
            # A malformed key must fail loudly at first use rather than fall
            # back to the dev derivation, which would silently downgrade
            # production secrets to a SECRET_KEY-derived key.
            raise CredentialEncryptionError(
                "AURA_CREDENTIAL_ENCRYPTION_KEY is not a valid Fernet key "
                "(expected urlsafe-base64 of 32 bytes; generate one with "
                "`python -c \"from cryptography.fernet import Fernet; "
                "print(Fernet.generate_key().decode())\"`)."
            ) from exc

    if settings.is_production:
        raise CredentialEncryptionError(
            "AURA_CREDENTIAL_ENCRYPTION_KEY must be set in production. Stored "
            "data-source passwords would otherwise be protected by a key "
            "derived from SECRET_KEY, so rotating SECRET_KEY would make every "
            "saved connection undecryptable."
        )

    logger.warning(
        "AURA_CREDENTIAL_ENCRYPTION_KEY unset — deriving a development key "
        "from SECRET_KEY. Set an explicit key before storing real credentials.",
    )
    _fernet = Fernet(_derive_dev_key(settings.secret_key))
    return _fernet


def reset_cache() -> None:
    """Drop the cached Fernet so a settings change takes effect (tests)."""
    global _fernet
    _fernet = None


def encrypt_secret(plaintext: str) -> str:
    """Encrypt a credential for storage. Returns an opaque token."""
    if plaintext is None:
        raise CredentialEncryptionError("cannot encrypt None")
    return _get_fernet().encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt_secret(token: str) -> str:
    """Recover a credential. Raises rather than returning a wrong value.

    A wrong-but-plausible return would surface as a confusing authentication
    failure against the customer's own database; failing loudly points at the
    real cause (a changed or rotated encryption key).
    """
    if not token:
        raise CredentialEncryptionError("cannot decrypt an empty token")
    try:
        return _get_fernet().decrypt(token.encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise CredentialEncryptionError(
            "stored credential could not be decrypted — the encryption key "
            "has most likely changed since it was saved."
        ) from exc
