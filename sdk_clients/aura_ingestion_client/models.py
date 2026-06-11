"""
Auto-generated Pydantic v2 models — DO NOT EDIT BY HAND.

Regenerate with:

    python scripts/generate_sdk.py \
        --openapi aurabackend/openapi.json \
        --output sdk_clients/aura_gateway_client \
        --package-name aura_gateway_client

Source schema fingerprint: c54d04039a798c60
"""
from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel


class HTTPValidationError(BaseModel):
    detail: Optional[List["ValidationError"]] = None


class RawIngestionPayload(BaseModel):
    batch_id: str
    entries: List[Dict[str, Any]]
    tenant_id: str


class ValidationError(BaseModel):
    loc: List[Union[str, int]]
    msg: str
    type: str
    ctx: Optional[Dict[str, Any]] = None
    input: Optional[Any] = None

