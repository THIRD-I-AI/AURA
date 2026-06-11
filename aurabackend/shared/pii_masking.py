import json
import logging
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
