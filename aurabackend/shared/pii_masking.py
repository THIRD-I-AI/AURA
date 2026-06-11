import hashlib
import hmac
import json
import logging
import os
from typing import Any

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
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

async def set_body(request: Request, body: bytes):
    async def receive() -> Message:
        return {"type": "http.request", "body": body}
    request._receive = receive

class PIIMaskingMiddleware(BaseHTTPMiddleware):
    """
    Perimeter Defense: Intercepts raw JSON bodies at the API Gateway level
    and permanently redacts restricted PII fields before they are parsed by
    Pydantic or routed to internal Kafka streams.
    """
    async def dispatch(self, request: Request, call_next):
        # We only care about modifying JSON payloads (e.g. POST /api/v1/ingest)
        if request.method in ["POST", "PUT", "PATCH"] and "application/json" in request.headers.get("content-type", ""):
            try:
                body_bytes = await request.body()
                if body_bytes:
                    payload = json.loads(body_bytes)
                    redacted_payload = redact_pii(payload)

                    # Re-serialize and inject the cleansed body back into the request stream
                    cleansed_bytes = json.dumps(redacted_payload).encode("utf-8")
                    await set_body(request, cleansed_bytes)

            except json.JSONDecodeError:
                # If it's not valid JSON, we can't redact it. Let the downstream validation handle the 422.
                pass
            except Exception as e:
                logger.error(f"PIIMaskingMiddleware error: {e}")

        response = await call_next(request)
        return response
