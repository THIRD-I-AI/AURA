import hashlib
import hmac
import json
import logging
import os
import re
from typing import Any

from starlette.types import Message

logger = logging.getLogger("aura.pii_masking")

# BUG-115: the original PII_KEYS was an exact-match set -- "customer_ssn",
# "billing_email", "dob", "credit_card_number", "address" etc. all sailed
# through untouched. Matched as a substring instead (below), which trades a
# few over-redacted benign fields (e.g. "phone_type") for never missing a
# real PII field spelled slightly differently -- the safe direction for a
# perimeter-defense control.
PII_KEYWORDS = frozenset({
    "ssn", "social_security", "employee_name", "first_name", "last_name",
    "phone", "email", "dob", "date_of_birth", "credit_card", "card_number",
    "national_id", "passport", "ip_address",
})

# BUG-115: key-based redaction alone misses PII embedded inside an
# unrelated field's free-text VALUE (e.g. a "notes" field containing an
# SSN or email). These patterns catch the common, high-confidence cases.
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_SSN_RE = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
_CC_RE = re.compile(r"\b(?:\d[ -]?){13,19}\b")


def _key_is_pii(key: Any) -> bool:
    lowered = str(key).lower()
    return any(kw in lowered for kw in PII_KEYWORDS)


def _scrub_value_text(value: str) -> str:
    scrubbed = _EMAIL_RE.sub("[REDACTED_EMAIL]", value)
    scrubbed = _SSN_RE.sub("[REDACTED_SSN]", scrubbed)
    scrubbed = _CC_RE.sub("[REDACTED_CC]", scrubbed)
    return scrubbed


def redact_pii(data: Any) -> Any:
    """
    Recursively scrubs PII from dictionaries or lists.
    """
    if isinstance(data, dict):
        new_data = {}
        for k, v in data.items():
            if _key_is_pii(k):
                new_data[k] = "[REDACTED]"
            else:
                new_data[k] = redact_pii(v)
        return new_data
    elif isinstance(data, list):
        return [redact_pii(item) for item in data]
    elif isinstance(data, str):
        return _scrub_value_text(data)
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
            if _key_is_pii(k) else tokenize_pii(v, context=context)
            for k, v in data.items()
        }
    elif isinstance(data, list):
        return [tokenize_pii(item, context=context) for item in data]
    elif isinstance(data, str):
        # BUG-115: PII embedded in a free-text value has no (context, field)
        # boundary to key an HMAC token to -- redact with a fixed placeholder
        # instead. This still upholds the invariant that matters here (raw
        # PII never enters the stream); it just isn't correlatable like a
        # real field-keyed token would be.
        return _scrub_value_text(data)
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
