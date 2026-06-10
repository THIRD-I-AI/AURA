"""ED25519 JWKS + soft revocation over AURA's persistent signing key.

This module no longer holds its own keys: it reflects the ONE persistent key
managed by ``signing.py`` (so historical signatures stay verifiable across
restarts), and persists a small revocation set. Findings/documents are signed
directly via ``signing.sign_bytes``.
"""
import json
import logging
import os
from pathlib import Path
from typing import Dict, Set

from . import signing

logger = logging.getLogger("aura.cryptography")

_KID = "aura-ed25519"


def _revoked_path() -> Path:
    key_dir = os.getenv("AURA_SIGNING_KEY_DIR", "data/keys").strip() or "data/keys"
    return Path(key_dir) / "revoked_kids.json"


def _load_revoked() -> Set[str]:
    try:
        return set(json.loads(_revoked_path().read_text(encoding="utf-8")))
    except Exception:
        return set()


def is_revoked(kid: str = _KID) -> bool:
    return kid in _load_revoked()


def soft_revoke_key(kid: str = _KID) -> None:
    """Soft revocation: flag the kid for FUTURE signing (historical signatures
    stay valid via JWKS). Persisted so it survives restarts."""
    revoked = _load_revoked()
    revoked.add(kid)
    path = _revoked_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(sorted(revoked)), encoding="utf-8")
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
