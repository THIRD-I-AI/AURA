"""BUG-221: a malformed bearer token must not echo PyJWT's internal error text."""
import pytest
from fastapi.testclient import TestClient

from shared.auth import decode_access_token
from shared.exceptions import AuthenticationError

MALFORMED = ["not.a.jwt", "abc", "eyJhbGciOiJIUzI1NiJ9.e30.x"]
LEAKY_FRAGMENTS = ("codec", "padding", "segments", "header string", "byte 0x")


@pytest.mark.parametrize("token", MALFORMED)
def test_decode_access_token_raises_a_curated_message(token):
    with pytest.raises(AuthenticationError) as exc:
        decode_access_token(token)
    assert exc.value.message == "Invalid token"


@pytest.mark.parametrize("token", MALFORMED)
def test_protected_route_response_does_not_echo_library_text(token):
    from api_gateway.main import app

    resp = TestClient(app).get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401, resp.text
    body = resp.text.lower()
    assert not any(f in body for f in LEAKY_FRAGMENTS), resp.text


def test_expired_token_message_is_unchanged():
    from datetime import timedelta

    from shared.auth import create_access_token

    token = create_access_token({"sub": "u"}, expires_delta=timedelta(seconds=-1))
    with pytest.raises(AuthenticationError) as exc:
        decode_access_token(token)
    assert exc.value.message == "Token has expired"
