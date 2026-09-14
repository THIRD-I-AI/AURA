"""
Synthetic Data Router
=====================
Enterprise synthetic dataset generation at GB / TB / PB scale.

Endpoints (mounted under /api/v1):
  POST /synthetic/plan            – dry-run: rows/files/bytes plan, no data
  POST /synthetic/generate        – launch a background generation job
  GET  /synthetic/jobs            – list jobs
  GET  /synthetic/jobs/{job_id}   – poll one job's progress/result

The heavy generation runs in a worker thread (CPU-bound, releases the
event loop) and streams progress into an in-memory job record. The sink
URI is cloud-agnostic (file:// / s3:// / gs:// / abfs://) via pyarrow.fs.
"""
from __future__ import annotations

import asyncio
import os
import threading
import time
import uuid as _uuid
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from shared.error_handler import sanitize_error
from shared.logging_config import get_logger
from shared.storage.base import tenant_slug
from synthetic import (
    ColumnSpec,
    SyntheticDatasetWriter,
    TableSchema,
    parse_size,
    plan_generation,
)

from .workspaces import _request_tenant, current_workspace_id

# BUG-058: file:// destinations are confined to this per-tenant root
# (mirrors the tenant_slug() sandboxing used for uploads/pipeline/ETL
# outputs elsewhere). Cloud destinations (s3://, gs://, abfs://) are
# deliberately left unconfined -- this is an enterprise BYOC feature
# where the caller names their OWN cloud destination by design; the
# local-filesystem case is the one with no ambiguity (it always uses
# this server's own disk and, for file://, its own credentials).
_SYNTHETIC_OUTPUT_ROOT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "data", "synthetic",
)


def _confine_output_uri(output_uri: str, tenant: Optional[str], job_id: str) -> str:
    """Return an output_uri safe to hand to SyntheticDatasetWriter.

    Cloud URIs (s3/gs/abfs) pass through unchanged -- BYOC by design.
    A local path/file:// URI is rewritten to live under this tenant's
    own subdirectory, keeping only the caller's requested basename (a
    prefix, not a directory to escape through) -- matches the
    tenant_slug() confinement pattern used for uploads/pipeline/ETL
    outputs elsewhere. job_id makes every call's output directory unique
    so two calls requesting the same basename (repeated runs, concurrent
    tenants after tenant_slug collapses two similar ids, tests) never
    collide or silently reuse a previous run's files.
    """
    parsed = urlparse(output_uri)
    scheme = parsed.scheme.lower()
    if len(scheme) == 1:
        # urlparse misreads a Windows drive letter ("C:/...") as a
        # single-char scheme -- it's a local path, not a URI scheme.
        scheme = ""
    if scheme not in ("", "file", "s3", "gs", "abfs"):
        raise ValueError(f"unsupported output_uri scheme: {scheme!r}")
    if scheme not in ("", "file"):
        return output_uri

    raw_path = parsed.path if scheme == "file" else output_uri
    prefix = Path(raw_path.replace("\\", "/")).name or "dataset"
    tenant_root = os.path.join(_SYNTHETIC_OUTPUT_ROOT, tenant_slug(tenant), job_id)
    os.makedirs(tenant_root, exist_ok=True)
    confined = os.path.join(tenant_root, prefix)
    if os.path.commonpath((os.path.realpath(confined), os.path.realpath(tenant_root))) != os.path.realpath(tenant_root):
        # prefix somehow still escapes (e.g. Windows device name) -- fall
        # back to a fixed, always-safe name rather than fail the request.
        confined = os.path.join(tenant_root, "dataset")
    return confined

logger = get_logger("aura.api_gateway.synthetic")
router = APIRouter(tags=["Synthetic Data"])

# ── In-memory job store ─────────────────────────────────────────────
_jobs_lock = threading.Lock()
_jobs: Dict[str, Dict[str, Any]] = {}


# ── Request / response models ───────────────────────────────────────
class ColumnSpecModel(BaseModel):
    name: str
    dtype: str = "float"
    dist: str = "uniform"
    low: float = 0.0
    high: float = 1.0
    mean: float = 0.0
    std: float = 1.0
    lam: float = 1.0
    zipf_a: float = 2.0
    categories: Optional[List[str]] = None
    weights: Optional[List[float]] = None
    prefix: str = "val_"
    str_cardinality: int = 1000
    start_ts: float = 1_700_000_000.0
    end_ts: float = 1_800_000_000.0
    null_rate: float = 0.0
    decimals: Optional[int] = None


class SchemaModel(BaseModel):
    name: str = "synthetic"
    columns: List[ColumnSpecModel]


class PlanRequest(BaseModel):
    schema_def: SchemaModel = Field(..., alias="schema")
    target_size: str = Field(..., description="e.g. 500MB, 1TB, 2PiB")
    chunk_rows: int = 1_000_000
    file_target_bytes: int = 128 * 10**6

    class Config:
        populate_by_name = True


class GenerateRequest(PlanRequest):
    output_uri: str = Field(..., description="file:///path or s3://bucket/prefix or gs://…")
    seed: int = 0
    compression: str = "snappy"
    max_files: Optional[int] = Field(None, description="cap files (bounded/preview run)")


def _build_schema(sm: SchemaModel) -> TableSchema:
    cols = [ColumnSpec(**c.model_dump()) for c in sm.columns]
    return TableSchema(name=sm.name, columns=cols)


# ── Endpoints ───────────────────────────────────────────────────────
@router.post("/synthetic/plan")
async def synthetic_plan(req: PlanRequest):
    """Compute an a-priori rows/files/bytes plan without writing data."""
    try:
        schema = _build_schema(req.schema_def)
        target = parse_size(req.target_size)
        plan = plan_generation(
            schema, target,
            chunk_rows=req.chunk_rows,
            file_target_bytes=req.file_target_bytes,
        )
        return {"success": True, "plan": plan.to_dict(), "schema": schema.to_dict()}
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


def _run_job(job_id: str, req: GenerateRequest) -> None:
    """Worker-thread body: generate the dataset, updating the job record."""
    def progress_cb(p: Dict[str, Any]) -> None:
        with _jobs_lock:
            _jobs[job_id]["progress"] = p

    try:
        schema = _build_schema(req.schema_def)
        target = parse_size(req.target_size)
        writer = SyntheticDatasetWriter(
            schema, seed=req.seed, compression=req.compression,
            chunk_rows=req.chunk_rows, file_target_bytes=req.file_target_bytes,
        )
        with _jobs_lock:
            _jobs[job_id]["status"] = "running"
            _jobs[job_id]["plan"] = writer.plan(target).to_dict()
        result = writer.generate(
            req.output_uri, target,
            dataset_name=schema.name, max_files=req.max_files,
            progress_cb=progress_cb,
        )
        with _jobs_lock:
            _jobs[job_id]["status"] = "completed"
            _jobs[job_id]["result"] = result.to_dict()
            _jobs[job_id]["finished_at"] = time.time()
    except Exception as exc:  # noqa: BLE001 — surface any failure into the job record
        with _jobs_lock:
            _jobs[job_id]["status"] = "failed"
            _jobs[job_id]["error"] = sanitize_error(exc, logger=logger, context="synthetic generate")
            _jobs[job_id]["finished_at"] = time.time()


@router.post("/synthetic/generate")
async def synthetic_generate(req: GenerateRequest, request: Request):
    """Launch a background generation job; returns a job_id to poll."""
    job_id = _uuid.uuid4().hex[:12]
    try:
        # Validate schema + size eagerly so bad requests fail fast (not in the thread).
        _build_schema(req.schema_def)
        parse_size(req.target_size)
        # BUG-058: confine a local output_uri to the caller's own tenant
        # subdirectory before it ever reaches the writer -- was a fully
        # caller-controlled local-filesystem write destination. job_id
        # keeps each call's output unique so repeated/concurrent calls
        # with the same requested name never collide.
        req.output_uri = _confine_output_uri(req.output_uri, _request_tenant(request), job_id)
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

    with _jobs_lock:
        _jobs[job_id] = {
            "job_id": job_id,
            "workspace_id": current_workspace_id(request),
            "status": "queued",
            "output_uri": req.output_uri,
            "target_size": req.target_size,
            "created_at": time.time(),
            "progress": None,
            "result": None,
            "error": None,
        }
    # Run CPU-bound generation off the event loop.
    asyncio.get_event_loop().run_in_executor(None, _run_job, job_id, req)
    return {"success": True, "job_id": job_id, "status": "queued"}


@router.get("/synthetic/jobs")
async def list_synthetic_jobs(request: Request):
    """List the CALLER's synthetic-data jobs, scoped by the verified workspace.

    _jobs was a bare in-memory dict with no tenant field at all -- every
    caller saw every other tenant's job list (output URIs, schema, sizes).
    """
    workspace_id = current_workspace_id(request)
    with _jobs_lock:
        jobs = [j for j in _jobs.values() if j.get("workspace_id") == workspace_id]
        return {"jobs": sorted(jobs, key=lambda j: j["created_at"], reverse=True)}


@router.get("/synthetic/jobs/{job_id}")
async def get_synthetic_job(job_id: str, request: Request):
    """Fetch one job, scoped to the caller's workspace.

    Previously had no ownership check at all -- any tenant could poll
    another tenant's job by id. A mismatched workspace_id now reports the
    same 404 as a genuinely unknown id, never confirming the job exists
    under a different tenant.
    """
    with _jobs_lock:
        job = _jobs.get(job_id)
        if job is None or job.get("workspace_id") != current_workspace_id(request):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"job {job_id} not found")
        return dict(job)
