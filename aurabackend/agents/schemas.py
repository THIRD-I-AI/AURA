"""
LangGraph Orchestrator — Strict Pydantic v2 Schemas
====================================================
Every node in the orchestrator graph reads from / writes to a typed slot on
``OrchestratorState``.  Returning a dict that does not validate against the
slot's schema raises ``pydantic.ValidationError`` at the node boundary, which
is converted into a structured ``NodeError`` and routed to END — malformed
agent output never reaches a downstream node.

This is the "Pydantic AI" guarantee referenced in the upgrade plan: type
safety enforced at agent I/O boundaries, not just at the API edge.
"""
from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

# ── Planner ───────────────────────────────────────────────────────────

PlanTaskType = Literal[
    "ingest", "schema_design", "transform", "quality_check",
    "pipeline_build", "optimize", "execute_sql", "generate_sql", "monitor",
]


class PlanTask(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    task_type: PlanTaskType
    description: str
    agent_name: str
    depends_on: List[str] = Field(default_factory=list)
    parameters: Dict[str, Any] = Field(default_factory=dict)


class PlannerOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan_id: str
    summary: str = ""
    estimated_duration_sec: int = Field(default=0, ge=0)
    tasks: List[PlanTask] = Field(default_factory=list)

    @property
    def has_sql_path(self) -> bool:
        return any(
            t.agent_name == "SQLGeneratorAgent" or t.task_type in {"generate_sql", "execute_sql"}
            for t in self.tasks
        )


# ── SQL Generation ────────────────────────────────────────────────────

class SQLGenOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sql: str = Field(min_length=1)
    explanation: Optional[str] = None
    dialect: str = "duckdb"


# ── Execution ─────────────────────────────────────────────────────────

class ExecutionOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    columns: List[str] = Field(default_factory=list)
    records: List[Dict[str, Any]] = Field(default_factory=list)
    rows: List[List[Any]] = Field(default_factory=list)
    row_count: int = Field(default=0, ge=0)
    truncated: bool = False


# ── Visualization ─────────────────────────────────────────────────────

ChartType = Literal[
    "bar", "stacked_bar", "line", "multi_line", "area",
    "pie", "scatter", "histogram", "kpi", "table",
]


class ChartSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: ChartType
    x: Optional[str] = None
    y: List[str] = Field(default_factory=list)
    title: str = ""
    reason: str = ""


class VisualizationOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chart: Optional[ChartSpec] = None
    skipped_reason: Optional[str] = None


# ── Analysis ──────────────────────────────────────────────────────────

class AnalysisOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conclusion: Optional[str] = None
    stats: Dict[str, Any] = Field(default_factory=dict)
    skipped_reason: Optional[str] = None


# ── Telemetry ─────────────────────────────────────────────────────────

class NodeError(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node: str
    message: str
    duration_ms: float = 0.0


# ── Top-level state ───────────────────────────────────────────────────

class OrchestratorState(BaseModel):
    """Single source of truth flowing through the LangGraph DAG."""

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    # Inputs
    user_prompt: str
    session_id: str
    files: List[str] = Field(default_factory=list)
    connection: Dict[str, Any] = Field(default_factory=dict)
    schema_context: Dict[str, Any] = Field(default_factory=dict)
    # Known-key catalogue lives in agents.base.AgentContextMetadata.
    # Kept as Dict[str, Any] at the Pydantic boundary so JSON input
    # from the API gateway round-trips without per-key validation.
    metadata: Dict[str, Any] = Field(default_factory=dict)

    # Per-node outputs (populated as the graph progresses)
    plan: Optional[PlannerOutput] = None
    sql: Optional[SQLGenOutput] = None
    execution: Optional[ExecutionOutput] = None
    visualization: Optional[VisualizationOutput] = None
    analysis: Optional[AnalysisOutput] = None

    # Telemetry
    errors: List[NodeError] = Field(default_factory=list)
    completed_nodes: List[str] = Field(default_factory=list)

    @property
    def succeeded(self) -> bool:
        return not self.errors
