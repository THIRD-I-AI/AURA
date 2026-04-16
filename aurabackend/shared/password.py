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


def hash_password(plain: str) -> str:
    """Return a bcrypt hash of *plain*."""
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    """Return True if *plain* matches *hashed*."""
    return bcrypt.checkpw(plain.encode(), hashed.encode())
