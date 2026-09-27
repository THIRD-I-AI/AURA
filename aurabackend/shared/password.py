"""
AURA Password Utilities
========================
Bcrypt-based password hashing for the ``password`` auth mode.

Usage:
    from shared.password import hash_password, verify_password

    hashed = hash_password("hunter2")
    assert verify_password("hunter2", hashed)
"""
from __future__ import annotations

import bcrypt

# bcrypt only reads the first 72 bytes. bcrypt < 5.0 silently truncated longer
# input; 5.0 raises ValueError instead, which surfaced as an unhandled 500 on
# /auth/register and /auth/token (BUG-222).
MAX_PASSWORD_BYTES = 72


def hash_password(plain: str) -> str:
    """Return a bcrypt hash of *plain*. Callers validate length first."""
    encoded = plain.encode()
    if len(encoded) > MAX_PASSWORD_BYTES:
        raise ValueError(f"password is longer than {MAX_PASSWORD_BYTES} bytes")
    return bcrypt.hashpw(encoded, bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    """Return True if *plain* matches *hashed*.

    Compares the first 72 bytes -- exactly what bcrypt < 5.0 did -- so an
    over-length login attempt is a plain mismatch (401), not a crash, and a
    user whose long password was truncated at registration can still sign in."""
    return bcrypt.checkpw(plain.encode()[:MAX_PASSWORD_BYTES], hashed.encode())
