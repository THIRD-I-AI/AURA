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
import time
import uuid as _uuid
from concurrent.futures import ThreadPoolExecutor
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

# BUG-338: jobs used to run on the event loop's DEFAULT executor -- the same small
# pool (min(32, cpus + 4) threads) that every asyncio.to_thread call in the gateway
# shares. A handful of multi-minute 1 GB jobs occupied all of it, and every
# offloaded DuckDB query, file read and storage call queued behind them. Generation
# gets its own bounded pool; extra jobs wait in it instead.
_GENERATION_POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix="synthetic-gen")

router = APIRouter(tags=["Synthetic Data"])

# Job records live in the gateway database (api_gateway.persistence), not in this
# process (BUG-430): with several gateway workers a poll could reach a worker that
# never saw the job, and a restart lost every job. The generation thread writes its
# updates back through the event loop that owns the database engine.

# Progress is written at most this often; status changes are always written.
_PROGRESS_WRITE_INTERVAL_S = 1.0


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
    prefix: str = Field("val_", max_length=64)  # BUG-370: bounds the row width
    str_cardinality: int = 1000
    start_ts: float = 1_700_000_000.0
    end_ts: float = 1_800_000_000.0
    null_rate: float = 0.0
    decimals: Optional[int] = None


class SchemaModel(BaseModel):
    name: str = "synthetic"
    columns: List[ColumnSpecModel] = Field(..., min_length=1, max_length=256)  # BUG-370


class PlanRequest(BaseModel):
    schema_def: SchemaModel = Field(..., alias="schema")
    target_size: str = Field(..., description="e.g. 500MB, 1TB, 2PiB")
    # BUG-227: bounded so one request cannot ask for a multi-GB chunk in memory.
    chunk_rows: int = Field(1_000_000, ge=1, le=1_000_000)
    file_target_bytes: int = Field(128 * 10**6, ge=10**6, le=10**9)

    class Config:
        populate_by_name = True


class GenerateRequest(PlanRequest):
    output_uri: str = Field(..., description="file:///path or s3://bucket/prefix or gs://…")
    seed: int = 0
    compression: str = "snappy"
    max_files: Optional[int] = Field(None, ge=1, le=10_000, description="cap files (bounded/preview run)")


def _max_generate_bytes() -> int:
    """Largest dataset one /synthetic/generate job may write (BUG-227). /synthetic/plan
    stays unbounded -- it only does arithmetic -- but generate writes to the server's disk."""
    try:
        return int(os.getenv("AURA_SYNTHETIC_MAX_BYTES", str(1024**3)))
    except ValueError:
        return 1024**3


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


def _run_job(job_id: str, req: GenerateRequest, loop: asyncio.AbstractEventLoop) -> None:
    """Worker-thread body: generate the dataset, recording progress in the database."""
    from api_gateway import persistence

    def record(**fields: Any) -> None:
        write = persistence.update_synthetic_job(job_id, fields, time.time())
        try:
            if loop.is_running():
                # The server's loop owns the async engine; run the write there.
                asyncio.run_coroutine_threadsafe(write, loop).result(timeout=30)
            else:
                # Only when that loop has stopped -- a test client that opens a fresh
                # loop per request -- does the thread run the write on its own.
                asyncio.run(write)
        except Exception as exc:  # noqa: BLE001 -- a lost progress write must not kill the job
            logger.warning("synthetic job %s: could not record %s: %s", job_id, sorted(fields), exc)

    last_progress = 0.0

    def progress_cb(p: Dict[str, Any]) -> None:
        nonlocal last_progress
        now = time.monotonic()
        if now - last_progress >= _PROGRESS_WRITE_INTERVAL_S:
            last_progress = now
            record(progress=p)

    try:
        schema = _build_schema(req.schema_def)
        target = parse_size(req.target_size)
        writer = SyntheticDatasetWriter(
            schema, seed=req.seed, compression=req.compression,
            chunk_rows=req.chunk_rows, file_target_bytes=req.file_target_bytes,
        )
        record(status="running", plan=writer.plan(target).to_dict())
        result = writer.generate(
            req.output_uri, target,
            dataset_name=schema.name, max_files=req.max_files,
            progress_cb=progress_cb,
        )
        record(status="completed", result=result.to_dict(), finished_at=time.time())
    except Exception as exc:  # noqa: BLE001 — surface any failure into the job record
        record(status="failed", error=sanitize_error(exc, logger=logger, context="synthetic generate"),
               finished_at=time.time())


@router.post("/synthetic/generate")
async def synthetic_generate(req: GenerateRequest, request: Request):
    """Launch a background generation job; returns a job_id to poll."""
    job_id = _uuid.uuid4().hex[:12]
    try:
        # Validate schema + size eagerly so bad requests fail fast (not in the thread).
        _build_schema(req.schema_def)
        target_bytes = parse_size(req.target_size)
        cap = _max_generate_bytes()
        if target_bytes > cap:
            raise ValueError(
                f"target_size {req.target_size} exceeds the {cap:,}-byte limit for one "
                "generate job (AURA_SYNTHETIC_MAX_BYTES); use /synthetic/plan to size larger datasets"
            )
        # BUG-058: confine a local output_uri to the caller's own tenant
        # subdirectory before it ever reaches the writer -- was a fully
        # caller-controlled local-filesystem write destination. job_id
        # keeps each call's output unique so repeated/concurrent calls
        # with the same requested name never collide.
        req.output_uri = _confine_output_uri(req.output_uri, _request_tenant(request), job_id)
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

    from api_gateway import persistence

    await persistence.insert_synthetic_job({
        "job_id": job_id,
        "workspace_id": current_workspace_id(request),
        "status": "queued",
        "output_uri": req.output_uri,
        "target_size": req.target_size,
        "created_at": time.time(),
        "progress": None,
        "result": None,
        "error": None,
    })
    # Run CPU-bound generation off the event loop, on this router's own pool.
    loop = asyncio.get_running_loop()
    loop.run_in_executor(_GENERATION_POOL, _run_job, job_id, req, loop)
    return {"success": True, "job_id": job_id, "status": "queued"}


@router.get("/synthetic/jobs")
async def list_synthetic_jobs(request: Request):
    """List the CALLER's synthetic-data jobs, scoped by the verified workspace.

    _jobs was a bare in-memory dict with no tenant field at all -- every
    caller saw every other tenant's job list (output URIs, schema, sizes).
    """
    from api_gateway import persistence

    return {"jobs": await persistence.list_synthetic_jobs(current_workspace_id(request))}


@router.get("/synthetic/jobs/{job_id}")
async def get_synthetic_job(job_id: str, request: Request):
    """Fetch one job, scoped to the caller's workspace.

    Previously had no ownership check at all -- any tenant could poll
    another tenant's job by id. A mismatched workspace_id now reports the
    same 404 as a genuinely unknown id, never confirming the job exists
    under a different tenant.
    """
    from api_gateway import persistence

    job = await persistence.get_synthetic_job(job_id, current_workspace_id(request))
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"job {job_id} not found")
    return job
