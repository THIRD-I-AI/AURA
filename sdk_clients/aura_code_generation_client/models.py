"""
Auto-generated Pydantic v2 models — DO NOT EDIT BY HAND.

Regenerate with:

    python scripts/generate_sdk.py \
        --openapi aurabackend/openapi.json \
        --output sdk_clients/aura_gateway_client \
        --package-name aura_gateway_client

Source schema fingerprint: 731ce0ed0c2c88a6
"""
from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel


class HTTPValidationError(BaseModel):
    detail: Optional[List["ValidationError"]] = None


class PlanStep(BaseModel):
    step: str
    chart_type: Optional[str] = None
    task: Optional[str] = None


class ValidationError(BaseModel):
    loc: List[Union[str, int]]
    msg: str
    type: str

