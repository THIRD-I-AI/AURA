"""
AURA Auth Tests
================
Tests for the dual-mode auth system (open / password).
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.password import hash_password, verify_password

# ── Password hashing ───────────────────────────────────────────────────

class TestPasswordHashing:
    def test_hash_roundtrip(self):
        plain = "super-secret-123"
        hashed = hash_password(plain)
        assert hashed != plain
        assert verify_password(plain, hashed)

    def test_wrong_password_rejected(self):
        hashed = hash_password("correct-password")
        assert not verify_password("wrong-password", hashed)

    def test_different_hashes_for_same_password(self):
        h1 = hash_password("same")
        h2 = hash_password("same")
        assert h1 != h2  # bcrypt uses random salt


# ── Auth endpoints ─────────────────────────────────────────────────────

@pytest.fixture()
def open_client(monkeypatch):
    """FastAPI TestClient in open auth mode with fresh DB."""
    monkeypatch.setenv("AURA_AUTH_MODE", "open")
    # Force fresh settings
    from shared import config as config_mod
    config_mod.get_settings.cache_clear()

    from fastapi.testclient import TestClient

    from api_gateway.main import app
    return TestClient(app)


@pytest.fixture()
def password_client(monkeypatch, tmp_path):
    """FastAPI TestClient in password auth mode with file-based SQLite."""
    db_path = tmp_path / "test.db"

    # Patch the module-level settings object that the auth router already imported
    from shared.config import settings
    monkeypatch.setattr(settings, "auth_mode", "password")

    # Create tables using a temporary sync engine (avoids event-loop mismatch)
    from sqlalchemy import create_engine

    from metadata_store.models import Base
    sync_engine = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(sync_engine)
    sync_engine.dispose()

    # Point the async engine at the DB file (tables already exist)
    from metadata_store import db as db_mod
    db_mod._engine = None
    db_mod._session_factory = None
    db_mod.DATABASE_URL = f"sqlite+aiosqlite:///{db_path}"

    from fastapi.testclient import TestClient

    from api_gateway.main import app
    return TestClient(app)


V1 = "/api/v1"


class TestOpenModeAuth:
    def test_open_mode_issues_token(self, open_client):
        resp = open_client.post(f"{V1}/auth/token", json={
            "user_id": "test-user",
            "role": "admin",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "access_token" in data
        assert data["token_type"] == "bearer"

    def test_open_mode_requires_user_id(self, open_client):
        resp = open_client.post(f"{V1}/auth/token", json={
            "email": "test@example.com",
        })
        assert resp.status_code == 422  # ValidationError

    def test_me_with_valid_token(self, open_client):
        # Get a token
        resp = open_client.post(f"{V1}/auth/token", json={
            "user_id": "me-test",
            "email": "me@test.com",
            "role": "admin",
        })
        token = resp.json()["access_token"]

        # Use it
        resp = open_client.get(f"{V1}/auth/me", headers={
            "Authorization": f"Bearer {token}",
        })
        assert resp.status_code == 200
        me = resp.json()
        assert me["sub"] == "me-test"
        assert me["email"] == "me@test.com"
        assert me["role"] == "admin"


class TestPasswordModeAuth:
    def test_register_and_login(self, password_client):
        # Register
        resp = password_client.post(f"{V1}/auth/register", json={
            "email": "alice@example.com",
            "password": "strong-pass-123",
            "name": "Alice",
        })
        assert resp.status_code == 201
        user = resp.json()
        assert user["email"] == "alice@example.com"
        assert user["name"] == "Alice"
        assert user["role"] == "user"

        # Login with correct password
        resp = password_client.post(f"{V1}/auth/token", json={
            "email": "alice@example.com",
            "password": "strong-pass-123",
        })
        assert resp.status_code == 200
        assert "access_token" in resp.json()

    def test_login_wrong_password_rejected(self, password_client):
        # Register first
        password_client.post(f"{V1}/auth/register", json={
            "email": "bob@example.com",
            "password": "correct-pass-456",
            "name": "Bob",
        })

        # Login with wrong password
        resp = password_client.post(f"{V1}/auth/token", json={
            "email": "bob@example.com",
            "password": "wrong-password",
        })
        assert resp.status_code == 401

    def test_login_nonexistent_user_rejected(self, password_client):
        resp = password_client.post(f"{V1}/auth/token", json={
            "email": "nobody@example.com",
            "password": "doesnt-matter",
        })
        assert resp.status_code == 401

    def test_register_duplicate_email_rejected(self, password_client):
        password_client.post(f"{V1}/auth/register", json={
            "email": "dupe@example.com",
            "password": "password-123",
            "name": "First",
        })
        resp = password_client.post(f"{V1}/auth/register", json={
            "email": "dupe@example.com",
            "password": "password-456",
            "name": "Second",
        })
        assert resp.status_code == 409

    def test_password_mode_requires_email_and_password(self, password_client):
        resp = password_client.post(f"{V1}/auth/token", json={
            "user_id": "not-enough",
        })
        assert resp.status_code == 422
