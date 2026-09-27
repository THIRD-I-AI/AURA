"""BUG-222: passwords over bcrypt's 72-byte limit must be a clean 422/401, not a 500."""
import pytest

from shared.password import MAX_PASSWORD_BYTES, hash_password, verify_password
from tests.test_auth import password_client  # noqa: F401  (fixture)

V1 = "/api/v1"


def test_hash_password_rejects_over_length_input_with_a_clear_error():
    with pytest.raises(ValueError, match="72 bytes"):
        hash_password("a" * 73)


def test_verify_password_does_not_raise_on_over_length_input():
    hashed = hash_password("a" * 72)
    # same first 72 bytes -> match (bcrypt < 5.0 semantics); otherwise a plain mismatch
    assert verify_password("a" * 100, hashed) is True
    assert verify_password("b" * 100, hashed) is False


def test_multibyte_characters_count_as_bytes():
    assert len(("é" * 36).encode()) == MAX_PASSWORD_BYTES
    hash_password("é" * 36)
    with pytest.raises(ValueError):
        hash_password("é" * 37)


def test_register_with_an_over_length_password_is_a_422_not_a_500(password_client):  # noqa: F811 (pytest fixture)
    resp = password_client.post(f"{V1}/auth/register", json={
        "email": "long@example.com", "password": "x" * 100, "name": "Long",
    })
    assert resp.status_code == 422, resp.text


def test_login_with_an_over_length_password_is_a_401_not_a_500(password_client):  # noqa: F811 (pytest fixture)
    password_client.post(f"{V1}/auth/register", json={
        "email": "ok@example.com", "password": "strong-pass-123", "name": "Ok",
    })
    resp = password_client.post(f"{V1}/auth/token", json={
        "email": "ok@example.com", "password": "y" * 200,
    })
    assert resp.status_code == 401, resp.text
