"""ED25519 JWKS + soft revocation over AURA's persistent signing key.

This module no longer holds its own keys: it reflects the ONE persistent key
managed by ``signing.py`` (so historical signatures stay verifiable across
restarts), and persists a small revocation set. Findings/documents are signed
directly via ``signing.sign_bytes``.
"""
import json
import logging
import os
import threading
from pathlib import Path
from typing import Dict, Set

from . import signing

logger = logging.getLogger("aura.cryptography")

_KID = "aura-ed25519"


def _revoked_path() -> Path:
    key_dir = os.getenv("AURA_SIGNING_KEY_DIR", "data/keys").strip() or "data/keys"
    return Path(key_dir) / "revoked_kids.json"


class RevocationStateError(RuntimeError):
    """The revocation file exists but cannot be read or parsed."""


_revoke_lock = threading.Lock()


def _load_revoked() -> Set[str]:
    """The revoked kids. A MISSING file means nothing has been revoked; a file that
    exists but is unreadable/corrupt raises ``RevocationStateError`` (BUG-209: this
    used to return an empty set, so corruption silently un-revoked the key)."""
    path = _revoked_path()
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return set()
    except OSError as exc:
        raise RevocationStateError(f"cannot read {path}: {exc}") from exc
    try:
        data = json.loads(raw)
        if not isinstance(data, list):
            raise ValueError("expected a JSON list")
        return {str(k) for k in data}
    except ValueError as exc:
        raise RevocationStateError(f"corrupt revocation file {path}: {exc}") from exc


def is_revoked(kid: str = _KID) -> bool:
    """Fail CLOSED: if the revocation state cannot be trusted, report the key as
    revoked so callers stop signing with it (they already fall back to unsigned)."""
    try:
        return kid in _load_revoked()
    except RevocationStateError as exc:
        logger.error("Revocation state unreadable -- treating %s as REVOKED: %s", kid, exc)
        return True


def soft_revoke_key(kid: str = _KID) -> None:
    """Soft revocation: flag the kid for FUTURE signing (historical signatures
    stay valid via JWKS). Persisted so it survives restarts.

    The read-modify-write is serialised and the file is replaced atomically, so a
    crash mid-write cannot leave partial JSON and two revocations cannot lose an
    entry. An unreadable existing file is overwritten (the caller is revoking; the
    old content cannot be trusted anyway)."""
    with _revoke_lock:
        try:
            revoked = _load_revoked()
        except RevocationStateError as exc:
            logger.error("Revocation state unreadable while revoking %s: %s", kid, exc)
            revoked = set()
        revoked.add(kid)
        path = _revoked_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(sorted(revoked)), encoding="utf-8")
        os.replace(tmp, path)
    logger.warning("Key %s soft-revoked; historical signatures remain valid.", kid)


def get_jwks() -> Dict:
    """JWKS for the single persistent signing key. Empty if signing unavailable."""
    x = signing.public_key_raw_b64url()
    if x is None:
        return {"keys": []}
    return {"keys": [{
        "kty": "OKP", "crv": "Ed25519", "kid": _KID, "x": x,
        "revoked": is_revoked(_KID),
    }]}
