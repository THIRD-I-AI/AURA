"""
Agent metadata parameter types.

Each agent reads `ctx.metadata` to pick up agent-specific knobs that don't
fit the generic `AgentContext` fields (e.g. a DuckDB connection passed from
the chat router, drift_result for UASR agents, etc.).

These TypedDicts document what each agent expects and let mypy/pyright
catch typos and missing keys at the read sites — `total=False` because most
keys have sensible defaults via `.get(...)`.
"""
from __future__ import annotations

from typing import Any, Dict, List, TypedDict


class ExecutionAgentParams(TypedDict, total=False):
    """Read by agents.specialists.execution_agent.ExecutionAgent."""
    duckdb_con: Any  # duckdb.DuckDBPyConnection — typed as Any to avoid hard import


class IngestionAgentParams(TypedDict, total=False):
    """Read by agents.specialists.ingestion_agent.IngestionAgent."""
    files: List[str]


class SQLGeneratorAgentParams(TypedDict, total=False):
    """Read by agents.specialists.sql_generator_agent.SQLGeneratorAgent."""
    execute: bool


class MonitorThresholds(TypedDict, total=False):
    null_rate: float
    row_count_drop_pct: float
    latency_ms: float


class MonitorPipelineRef(TypedDict, total=False):
    pipeline_id: str
    source_id: str
    connection_id: str


class MonitorDataBatch(TypedDict, total=False):
    source_id: str
    columns: List[str]
    rows: List[Dict[str, Any]]


class MonitorAgentParams(TypedDict, total=False):
    """Read by agents.specialists.monitor_agent.MonitorAgent."""
    pipelines: List[MonitorPipelineRef]
    data_batches: List[MonitorDataBatch]
    check_services: bool
    thresholds: MonitorThresholds


class ReflectorAgentParams(TypedDict, total=False):
    """Read by uasr.reflector_agent.ReflectorAgent."""
    drift_result: Dict[str, Any]
    error_logs: List[Dict[str, Any]]


class ActuatorAgentParams(TypedDict, total=False):
    """Read by uasr.actuator_agent.ActuatorAgent."""
    diagnosis: Dict[str, Any]
    drift_result: Dict[str, Any]
    recovery_id: str
    drift_type: str
    drift_vector: Dict[str, Any]
