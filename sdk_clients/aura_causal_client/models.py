"""
Auto-generated Pydantic v2 models — DO NOT EDIT BY HAND.

Regenerate with:

    python scripts/generate_sdk.py \
        --openapi aurabackend/openapi.json \
        --output sdk_clients/aura_gateway_client \
        --package-name aura_gateway_client

Source schema fingerprint: 70aa5678bfd8f4f2
"""
from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel


class Attribution(BaseModel):
    cause: str
    score: float
    confidence: Optional[float] = None
    direction: Optional[str] = None


class CausalDiscoverRequest(BaseModel):
    anomaly_data: "DataSource"
    target_metric: str
    training_data: "DataSource"
    candidate_causes: Optional[List[str]] = None
    causal_graph_edges: Optional[List[List[Any]]] = None
    enforce_stationarity: Optional[bool] = None
    method: Optional[str] = None
    top_k: Optional[int] = None


class CausalDiscoverResponse(BaseModel):
    anomaly_count: int
    attributions: List["Attribution"]
    method_used: str
    sample_count: int
    summary: str
    target_metric: str
    stationarity: Optional["StationarityVerdict"] = None
    warnings: Optional[List[str]] = None


class DataSource(BaseModel):
    """Either inline rows OR a pointer to a DuckDB table (mutually exclusive)."""
    duckdb_path: Optional[str] = None
    duckdb_table: Optional[str] = None
    limit: Optional[int] = None
    rows: Optional[List[Dict[str, Any]]] = None
    where: Optional[str] = None


class HTTPValidationError(BaseModel):
    detail: Optional[List["ValidationError"]] = None


class StationarityVerdict(BaseModel):
    stationary: bool
    adf_p_value: Optional[float] = None
    reasons: Optional[List[str]] = None
    split_drift_sigma: Optional[float] = None


class ValidationError(BaseModel):
    loc: List[Union[str, int]]
    msg: str
    type: str
    ctx: Optional[Dict[str, Any]] = None
    input: Optional[Any] = None

