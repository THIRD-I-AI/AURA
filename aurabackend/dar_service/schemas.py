"""Pydantic state types for the DAR LangGraph DAG."""
from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class ColumnProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    column: str
    data_type: str
    null_rate: float = 0.0
    distinct_count: Optional[int] = None
    mean: Optional[float] = None
    std: Optional[float] = None
    min: Optional[Any] = None
    max: Optional[Any] = None
    top_values: List[Dict[str, Any]] = Field(default_factory=list)


class ResearchQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1)
    sql: str = Field(min_length=1)


class QueryResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str
    sql: str
    columns: List[str] = Field(default_factory=list)
    rows: List[List[Any]] = Field(default_factory=list)
    row_count: int = 0
    error: Optional[str] = None


class Finding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str
    sql: str
    finding_type: Literal["anomaly", "trend", "correlation", "summary"]
    summary: str
    score: float = Field(ge=0.0, le=1.0)
    is_anomaly: bool = False
    payload: Dict[str, Any] = Field(default_factory=dict)


class NodeError(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node: str
    message: str
    duration_ms: float = 0.0


class DARState(BaseModel):
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    run_id: str
    source_id: str
    table_name: str
    duckdb_path: str

    # Per-node outputs
    schema_columns: List[ColumnProfile] = Field(default_factory=list)
    profile_text: str = ""
    questions: List[ResearchQuestion] = Field(default_factory=list)
    query_results: List[QueryResult] = Field(default_factory=list)
    findings: List[Finding] = Field(default_factory=list)
    persisted_ids: List[str] = Field(default_factory=list)

    errors: List[NodeError] = Field(default_factory=list)
    completed_nodes: List[str] = Field(default_factory=list)

    @property
    def succeeded(self) -> bool:
        return not self.errors
