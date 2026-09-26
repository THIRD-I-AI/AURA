"""
Causal Discovery FastAPI app
=============================
Launched alongside the other AURA microservices (suggested port: 8010).

    uvicorn causal_service.main:app --port 8010
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import List

import pandas as pd
from fastapi import HTTPException

from shared.service_factory import create_service
from shared.sql_expression_guard import validate_sql_expression
from shared.sql_identifiers import quote_identifier

from .discovery import attribute, dowhy_available, summarise
from .models import (
    Attribution,
    CausalDiscoverRequest,
    CausalDiscoverResponse,
    DataSource,
)

logger = logging.getLogger("aura.causal.main")


app = create_service(
    name="Causal Discovery",
    service_tag="causal_service",
    description=(
        "Root-cause attribution for anomalous metrics using DoWhy's "
        "Structural Causal Models (gcm.attribute_anomalies). Falls back "
        "to partial-correlation ranking when DoWhy is unavailable."
    ),
)


# ── Data loading helpers ──────────────────────────────────────────────

def _load(source: DataSource, role: str) -> pd.DataFrame:
    if source.rows is not None and source.duckdb_table is not None:
        raise HTTPException(400, f"{role}: provide rows OR duckdb_table, not both.")
    if source.rows is not None:
        if not source.rows:
            raise HTTPException(400, f"{role}: rows is empty.")
        return pd.DataFrame(source.rows)
    if source.duckdb_table is None:
        raise HTTPException(400, f"{role}: must supply rows or duckdb_table.")

    default_path = os.getenv("UASR_DUCKDB_PATH", "data/uasr_lake.duckdb")
    duckdb_path = source.duckdb_path or default_path
    # BUG-197: the request used to choose ANY database file on disk. Only files inside the
    # configured lake's directory may be opened.
    lake_dir = Path(default_path).resolve().parent
    if not Path(duckdb_path).resolve().is_relative_to(lake_dir):
        raise HTTPException(400, f"{role}: duckdb_path must be inside the configured data directory.")
    if source.where:
        # BUG-197: a caller-supplied WHERE is spliced into the query. Single statement only, and the
        # shared file/network-function guard (the connection is locked down as well, below).
        if ";" in source.where:
            raise HTTPException(400, f"{role}: where must be a single expression.")
        try:
            validate_sql_expression(source.where)
        except ValueError as exc:
            raise HTTPException(400, f"{role}: {exc}") from exc
    try:
        import duckdb
    except ImportError as exc:
        raise HTTPException(500, f"duckdb not installed: {exc}") from exc

    con = None
    try:
        con = duckdb.connect(duckdb_path, read_only=True)
        con.execute("SET enable_external_access=false")
        con.execute("SET lock_configuration=true")
        # Whitelist the table name via information_schema before quoting.
        ok = con.execute(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema='main' AND table_name=?",
            [source.duckdb_table],
        ).fetchone()
        if not ok:
            raise HTTPException(404, f"{role}: unknown DuckDB table {source.duckdb_table!r}")
        sql = f"SELECT * FROM {quote_identifier(source.duckdb_table)}"
        if source.where:
            sql += f" WHERE {source.where}"
        sql += f" LIMIT {int(source.limit or 10_000)}"
        return con.execute(sql).fetch_df()
    finally:
        if con is not None:
            try:
                con.close()
            except Exception as exc:
                logger.debug("duckdb close failed: %s", exc)


# ── Endpoint ──────────────────────────────────────────────────────────

@app.post("/causal/discover", response_model=CausalDiscoverResponse)
async def causal_discover(req: CausalDiscoverRequest) -> CausalDiscoverResponse:
    training_df = _load(req.training_data, "training_data")
    anomaly_df = _load(req.anomaly_data, "anomaly_data")

    if req.target_metric not in training_df.columns:
        raise HTTPException(400, f"target_metric {req.target_metric!r} not in training data columns.")
    if req.target_metric not in anomaly_df.columns:
        raise HTTPException(400, f"target_metric {req.target_metric!r} not in anomaly data columns.")

    # Default candidates: every numeric column except the target.
    candidates: List[str] = list(req.candidate_causes or [
        c for c in training_df.columns
        if c != req.target_metric and pd.api.types.is_numeric_dtype(training_df[c])
    ])
    if not candidates:
        raise HTTPException(400, "No candidate cause columns available — supply candidate_causes explicitly.")
    missing = [c for c in candidates if c not in training_df.columns]
    if missing:
        raise HTTPException(400, f"Candidate cause columns missing from training data: {missing}")

    attributions, method_used, warnings, verdict = attribute(
        training_df,
        anomaly_df,
        target=req.target_metric,
        candidates=candidates,
        edges=req.causal_graph_edges,
        method=req.method,
        top_k=req.top_k,
        enforce_stationarity=req.enforce_stationarity,
    )

    return CausalDiscoverResponse(
        target_metric=req.target_metric,
        method_used=method_used,
        sample_count=len(training_df),
        anomaly_count=len(anomaly_df),
        attributions=attributions,
        summary=summarise(attributions, req.target_metric, method_used),
        warnings=warnings,
        stationarity=verdict,
    )


@app.get("/causal/info")
async def causal_info() -> dict:
    """Diagnostic — which engines + guardrails are live in this deployment."""
    from .discovery import (
        _PINGOUIN_AVAILABLE,
        _STATSMODELS_AVAILABLE,
    )
    return {
        "dowhy_available": dowhy_available(),
        "pingouin_available": _PINGOUIN_AVAILABLE,
        "statsmodels_available": _STATSMODELS_AVAILABLE,
        "default_engine": "gcm" if dowhy_available() else "correlation",
        "stationarity_guard": "active" if _STATSMODELS_AVAILABLE else "split-mean drift only (statsmodels missing)",
        "duckdb_default_path": os.getenv("UASR_DUCKDB_PATH", "data/uasr_lake.duckdb"),
    }
