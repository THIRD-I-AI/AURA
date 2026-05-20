"""
Auto-generated Pydantic v2 models — DO NOT EDIT BY HAND.

Regenerate with:

    python scripts/generate_sdk.py \
        --openapi aurabackend/openapi.json \
        --output sdk_clients/aura_gateway_client \
        --package-name aura_gateway_client

Source schema fingerprint: 7ab69b05ad120206
"""
from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel


class HTTPValidationError(BaseModel):
    detail: Optional[List["ValidationError"]] = None


class InsightOut(BaseModel):
    created_at: str
    finding_type: str
    id: str
    is_anomaly: bool
    question: str
    run_id: Optional[str]
    score: float
    source_id: str
    sql_query: Optional[str]
    summary: str
    table_name: str
    payload: Optional[Dict[str, Any]] = None


class ResearchRunRequest(BaseModel):
    table_name: str
    duckdb_path: Optional[str] = None
    source_id: Optional[str] = None


class ValidationError(BaseModel):
    loc: List[Union[str, int]]
    msg: str
    type: str

