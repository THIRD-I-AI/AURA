import hashlib
import hmac
import json
import logging
import os
from typing import Any

from starlette.types import Message

logger = logging.getLogger("aura.pii_masking")

PII_KEYS = {"ssn", "social_security_number", "employee_name", "first_name", "last_name", "phone", "email"}

def redact_pii(data: Any) -> Any:
    """
    Recursively scrubs PII from dictionaries or lists.
    """
    if isinstance(data, dict):
        new_data = {}
        for k, v in data.items():
            if str(k).lower() in PII_KEYS:
                new_data[k] = "[REDACTED]"
            else:
                new_data[k] = redact_pii(v)
        return new_data
    elif isinstance(data, list):
        return [redact_pii(item) for item in data]
    else:
        return data


# ── S34d — deterministic keyed pseudonymization at egress ─────────────

def _pii_token(field: str, value: Any, context: str) -> str:
    """`PII-` + 12 hex of HMAC-SHA256(key, context|field|value).

    HMAC (not a plain hash) because names/SSNs are low-entropy and an
    unkeyed deterministic hash is dictionary-invertible. Context (tenant)
    and field salting keep token equality from leaking across tenants or
    across different PII fields of the same person.
    """
    key = os.environ["AURA_PII_TOKEN_KEY"]
    msg = f"{context}|{field}|{value}"
    digest = hmac.new(key.encode("utf-8"), msg.encode("utf-8"), hashlib.sha256)
    return f"PII-{digest.hexdigest()[:12]}"


def tokenize_pii(data: Any, *, context: str = "") -> Any:
    """Like redact_pii, but the same (context, field, value) always maps to
    the same token — auditors can correlate entities across findings
    without seeing raw PII. Requires AURA_PII_TOKEN_KEY."""
    if isinstance(data, dict):
        return {
            k: _pii_token(str(k).lower(), v, context)
            if str(k).lower() in PII_KEYS else tokenize_pii(v, context=context)
            for k, v in data.items()
        }
    elif isinstance(data, list):
        return [tokenize_pii(item, context=context) for item in data]
    return data


def mask_pii_egress(data: Any, *, context: str = "") -> Any:
    """Egress masking: correlatable tokens when AURA_PII_TOKEN_KEY is
    configured, plain redaction otherwise. Fail-safe — without a key no
    deterministic (invertible-by-dictionary) output is ever emitted."""
    if os.getenv("AURA_PII_TOKEN_KEY"):
        return tokenize_pii(data, context=context)
    return redact_pii(data)

class PIIMaskingMiddleware:
    """
    Perimeter Defense: masks restricted PII fields in inbound JSON bodies
    before they are parsed by Pydantic or routed to internal Kafka streams.
    With AURA_PII_TOKEN_KEY set the masking is deterministic tokenization
    (same entity → same token), so downstream fraud correlation (AS 2401
    duplicate/related-party patterns) survives while raw PII never enters
    the stream; unkeyed it falls back to [REDACTED].

    Implemented as a pure ASGI middleware that wraps ``receive``. The
    previous BaseHTTPMiddleware version mutated ``request._receive``, which
    current Starlette does NOT propagate through ``call_next`` — the
    perimeter was silently a no-op (caught by tests/test_ingestion_security).
    """

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http" or scope.get("method") not in ("POST", "PUT", "PATCH"):
            return await self.app(scope, receive, send)
        headers = {k.decode("latin-1").lower(): v.decode("latin-1")
                   for k, v in scope.get("headers", [])}
        if "application/json" not in headers.get("content-type", ""):
            return await self.app(scope, receive, send)

        body = b""
        while True:
            message: Message = await receive()
            body += message.get("body", b"")
            if not message.get("more_body", False):
                break

        try:
            if body:
                payload = json.loads(body)
                context = str(payload.get("tenant_id", "")) if isinstance(payload, dict) else ""
                body = json.dumps(mask_pii_egress(payload, context=context)).encode("utf-8")
                # Masking changes the byte length — keep content-length honest.
                scope["headers"] = [
                    (k, v) if k != b"content-length" else (b"content-length", str(len(body)).encode())
                    for k, v in scope.get("headers", [])
                ]
        except json.JSONDecodeError:
            # Not valid JSON — pass through; downstream validation 422s.
            pass
        except Exception as exc:
            logger.error(f"PIIMaskingMiddleware error: {exc}")

        replayed = False

        async def cleansed_receive() -> Message:
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        await self.app(scope, cleansed_receive, send)
