"""Pydantic API contracts for the Causal Discovery service."""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field


class DataSource(BaseModel):
    """Either inline rows OR a pointer to a DuckDB table (mutually exclusive)."""
    model_config = ConfigDict(extra="forbid")

    rows: Optional[List[Dict[str, Any]]] = None
    duckdb_table: Optional[str] = None
    duckdb_path: Optional[str] = None
    where: Optional[str] = None
    limit: Optional[int] = Field(default=10_000, ge=1, le=500_000)


class CausalDiscoverRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_metric: str = Field(..., description="Column whose anomaly we want to attribute.")
    training_data: DataSource = Field(..., description="Historical data used to fit the SCM.")
    anomaly_data: DataSource = Field(..., description="The anomalous batch to attribute.")

    candidate_causes: Optional[List[str]] = Field(
        default=None,
        description="Restrict candidate cause columns. If omitted, every "
                    "non-target numeric column in the training set is used.",
    )
    causal_graph_edges: Optional[List[Tuple[str, str]]] = Field(
        default=None,
        description="Optional prior DAG as a list of (parent, child) edges. "
                    "When omitted, a fully-connected candidate→target graph is used.",
    )
    method: str = Field(
        default="auto",
        description="'auto' (DoWhy gcm if available, else correlation), "
                    "'gcm' (force DoWhy), or 'correlation' (skip DoWhy).",
    )
    top_k: int = Field(default=5, ge=1, le=50)
    enforce_stationarity: bool = Field(
        default=True,
        description="When true (default) and method resolves to 'gcm', the "
                    "service runs an ADF test + split-mean drift check on "
                    "the target column over the training window. If the "
                    "training slice spans a regime change, the request is "
                    "refused instead of returning misleading attributions. "
                    "Set false to override (e.g. for synthetic regression tests).",
    )


class Attribution(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cause: str
    score: float = Field(..., description="Mean attribution score (gcm) or |partial corr| (fallback).")
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    direction: str = Field(default="unknown", description="'positive', 'negative', or 'unknown'.")


class StationarityVerdict(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stationary: bool
    adf_p_value: Optional[float] = None
    split_drift_sigma: Optional[float] = None
    reasons: List[str] = Field(default_factory=list)


class CausalDiscoverResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_metric: str
    method_used: str
    sample_count: int
    anomaly_count: int
    attributions: List[Attribution]
    summary: str
    warnings: List[str] = Field(default_factory=list)
    stationarity: Optional[StationarityVerdict] = Field(
        default=None,
        description="Populated when the gcm engine ran the stationarity "
                    "guardrail. Absent for the correlation engine (which "
                    "doesn't require stationarity to produce a valid result).",
    )
