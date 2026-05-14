"""
Counterfactual Audit Engine — FastAPI app.

Suggested port 8012, after ``causal_service:8010`` and ``dar_service:8011``.

Job lifecycle is in-memory only for v1 (Sprint 8). Sprint 9 introduces a
Postgres-backed job table + signed PDF replay endpoint. The service is
pinned to one replica in Helm because the job state is process-local;
moving to a real queue is the Sprint 9 entry condition.
"""
from __future__ import annotations

import asyncio
import json
import logging
import pathlib
import uuid
from typing import Any, Dict, List, Optional

import pandas as pd
from fastapi import HTTPException
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field

from shared.service_factory import create_service

from . import pdf_renderer, persistence, signing
from .engine import dowhy_available, run_job
from .renderers import render
from .schemas import CounterfactualQuery

logger = logging.getLogger("aura.counterfactual.main")

app = create_service(
    name="Counterfactual Audit Engine",
    service_tag="counterfactual_service",
    description=(
        "Causal counterfactual estimation with hash-sealed audit artifacts. "
        "Four estimators × four refuters × an adversarial critic per job."
    ),
)


# ── In-memory state (v1) ──────────────────────────────────────────────

_jobs: Dict[str, Dict[str, Any]] = {}    # job_id → {state, artifact, error}
_datasets: Dict[str, pd.DataFrame] = {}  # source_id → DataFrame


def register_dataset(source_id: str, df: pd.DataFrame) -> None:
    """Pre-register a DataFrame under a source_id.

    Used by the test suite and by future ingestion paths that want to
    submit a counterfactual job against an already-loaded frame without
    going through the file-based resolver.
    """
    _datasets[source_id] = df


def _resolve_dataset(source_id: str) -> pd.DataFrame:
    if source_id in _datasets:
        return _datasets[source_id].copy()

    if source_id.startswith("uploaded_file:"):
        from shared.data_utils import _READ_FN_BY_EXT  # type: ignore
        name = source_id.split(":", 1)[1]
        for d in (
            pathlib.Path("data/uploads"),
            pathlib.Path("api_gateway/uploads"),
            pathlib.Path("uploads"),
        ):
            p = d / name
            if p.exists() and p.suffix.lower() in _READ_FN_BY_EXT:
                # Use the same read function the chat upload pipeline does
                # so column names come back identical.
                read_fn = _READ_FN_BY_EXT[p.suffix.lower()]
                if read_fn == "read_csv_auto":
                    return pd.read_csv(p)
                if read_fn == "read_parquet":
                    return pd.read_parquet(p)
                if read_fn == "read_json_auto":
                    return pd.read_json(p)
        raise HTTPException(404, f"file not found in any uploads dir: {name}")

    raise HTTPException(404, f"unknown dataset source_id: {source_id!r}")


# ── Job worker ────────────────────────────────────────────────────────

async def _run_async(job_id: str, query: CounterfactualQuery) -> None:
    _jobs[job_id]["state"] = "running"
    try:
        df = _resolve_dataset(query.dataset.source_id)
        artifact = await run_job(query, df=df)
        artifact.rendered = render(artifact, query.audience)
        _jobs[job_id].update(
            state="succeeded",
            artifact=artifact.model_dump(mode="json"),
        )
    except HTTPException as exc:
        _jobs[job_id].update(state="failed", error=f"HTTP {exc.status_code}: {exc.detail}")
    except Exception as exc:
        logger.exception("Counterfactual job %s failed", job_id)
        _jobs[job_id].update(state="failed", error=f"{type(exc).__name__}: {exc}")


# ── Endpoints ─────────────────────────────────────────────────────────

@app.post("/counterfactual/jobs")
async def submit_job(query: CounterfactualQuery) -> Dict[str, str]:
    job_id = f"ca_{uuid.uuid4().hex[:12]}"
    _jobs[job_id] = {"state": "queued", "artifact": None, "error": None}
    # Hold the Task reference inside the job record. Without this, the
    # task is eligible for garbage collection as soon as submit_job
    # returns — Python 3.11+ asyncio gives "Task was destroyed but it
    # is pending!" and the work silently never completes.
    _jobs[job_id]["_task"] = asyncio.create_task(_run_async(job_id, query))
    return {"job_id": job_id}


@app.get("/counterfactual/jobs/{job_id}")
async def get_job(job_id: str) -> Dict[str, Any]:
    if job_id not in _jobs:
        raise HTTPException(404, f"job {job_id} not found")
    j = _jobs[job_id]
    # Don't leak the Task object through the JSON response.
    return {
        "job_id": job_id,
        "state": j["state"],
        "artifact": j.get("artifact"),
        "error": j.get("error"),
    }


@app.get("/counterfactual/info")
async def info() -> Dict[str, Any]:
    return {
        "engine_version": "0.2.0",
        "dowhy_available": dowhy_available(),
        "signing_available": signing.signing_available(),
        "signing_key_source": signing.signing_key_source(),
        "pdf_available": pdf_renderer.pdf_available(),
        "estimators": ["linear_regression", "ipw", "psm", "double_ml"],
        "refuters":   ["random_common_cause", "placebo", "data_subset", "sensitivity"],
        "audiences":  ["operator", "auditor", "analyst"],
    }


# ── Sprint 9 — Auditor view ───────────────────────────────────────────

@app.get("/counterfactual/artifacts/{record_hash}")
async def get_artifact(record_hash: str) -> Dict[str, Any]:
    """Replay endpoint — returns the persisted artifact dict.

    Byte-identical to the artifact produced when the original job
    completed: replay reads the canonical-JSON bytes that the engine
    persisted, parsed back into a dict for the JSON response. The
    audit_record_hash in the body must equal the URL parameter.
    """
    art = persistence.read_artifact(record_hash)
    if art is None:
        raise HTTPException(404, f"artifact {record_hash} not found")
    return art


@app.get("/counterfactual/artifacts/{record_hash}/report.pdf")
async def get_artifact_pdf(record_hash: str) -> Response:
    """Auditor-grade printable report.

    Returns 501 (Not Implemented) when the deployment lacks reportlab.
    501 — not 503 — because the absence is *deterministic* per
    deployment: it's not a transient outage that the caller should
    retry, it's a feature this engine doesn't implement. SDK clients
    treat 501 as a definitive ``ServiceUnavailableError`` and skip
    retry, while 503 retains its conventional "service temporarily
    unavailable, please back off and retry" semantics elsewhere."""
    art = persistence.read_artifact(record_hash)
    if art is None:
        raise HTTPException(404, f"artifact {record_hash} not found")
    pdf_bytes = pdf_renderer.render_pdf(art)
    if pdf_bytes is None:
        raise HTTPException(
            501,
            "PDF renderer unavailable — install reportlab "
            "(pip install -r requirements-causal.txt) to enable.",
        )
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": (
                f'attachment; filename="counterfactual-{record_hash[:12]}.pdf"'
            ),
        },
    )


@app.get("/counterfactual/artifacts/{record_hash}/verify")
async def verify_artifact(record_hash: str) -> Dict[str, Any]:
    """Verify the persisted ED25519 signature against the persisted bytes.

    Returns ``{"verified": bool, "signature_status": ..., "reason": ...}``.
    Auditors can run this through any HTTP client without needing the
    private key.
    """
    art_bytes = persistence.read_artifact_bytes(record_hash)
    sig_b64 = persistence.read_signature(record_hash)
    if art_bytes is None:
        raise HTTPException(404, f"artifact {record_hash} not found")

    art_dict = persistence.read_artifact(record_hash) or {}
    sig_status = art_dict.get("signature_status", "unsigned")

    if sig_status == "unsigned" or sig_b64 is None:
        return {
            "record_hash": record_hash,
            "verified": False,
            "signature_status": "unsigned",
            "reason": "artifact was sealed without a signature",
        }

    # Reconstruct the exact bytes the engine signed. ``strip_for_hashing``
    # is the single source of truth shared with engine.run_job, so the
    # verify path can't drift from the sign path on nested exclude rules
    # like the per-estimate elapsed_ms strip Sprint 11 introduced. Prior
    # to Sprint 13 verify used a flat dict-comprehension that only
    # stripped top-level keys → every signed artifact returned
    # verified=False because elapsed_ms inside each estimate/refutation
    # made the reconstructed bytes diverge from what had actually been
    # signed.
    from .canonical import canonical_dumps
    from .engine import strip_for_hashing
    payload = strip_for_hashing(art_dict)
    payload_bytes = canonical_dumps(payload).encode("utf-8")

    ok = signing.verify_bytes(payload_bytes, sig_b64)
    return {
        "record_hash": record_hash,
        "verified": ok,
        "signature_status": sig_status,
        "signing_key_source": art_dict.get("signing_key_source"),
        "reason": "ok" if ok else "signature did not verify",
    }


@app.get("/counterfactual/public-key")
async def get_public_key() -> Dict[str, Any]:
    """Return the current ED25519 public key (PEM)."""
    pem = signing.public_key_pem()
    if pem is None:
        # 501 — feature unavailable in this deployment, not transient.
        raise HTTPException(501, "signing unavailable — cryptography not installed")
    return {"public_key_pem": pem, "key_source": signing.signing_key_source()}


# ── Sprint 13 — Auditor bulk-replay ───────────────────────────────────

class BulkReplayRequest(BaseModel):
    """Auditor batch input for periodic audit-chain integrity sweeps.

    The cap (256 hashes per call) is a soft DoS guard — bulk-replay
    re-hashes and re-verifies signatures for each artifact, which is
    cheap individually but expensive in aggregate. Auditors needing a
    larger sweep page through the endpoint client-side rather than
    parking a single thread on a 30-second request.
    """
    hashes: List[str] = Field(..., min_length=1, max_length=256)


def _verify_one_artifact(record_hash: str) -> Dict[str, Any]:
    """Stripped-down sibling of ``verify_artifact`` for bulk use.

    Returns the same shape per record but does NOT raise — every
    failure mode maps to a structured ``{status: ...}`` field so the
    streaming response can keep going past not-found / unsigned /
    verify-failed rows.
    """
    from .canonical import canonical_dumps
    from .engine import strip_for_hashing

    art_bytes = persistence.read_artifact_bytes(record_hash)
    if art_bytes is None:
        return {"record_hash": record_hash, "status": "not_found"}

    art_dict = persistence.read_artifact(record_hash) or {}
    sig_b64 = persistence.read_signature(record_hash)
    sig_status = art_dict.get("signature_status", "unsigned")

    if sig_status == "unsigned" or sig_b64 is None:
        return {
            "record_hash": record_hash,
            "status": "unsigned",
            "signature_status": "unsigned",
        }

    try:
        payload = strip_for_hashing(art_dict)
        payload_bytes = canonical_dumps(payload).encode("utf-8")
        ok = signing.verify_bytes(payload_bytes, sig_b64)
    except Exception as exc:
        logger.warning("Bulk verify failed for %s: %s", record_hash, exc)
        return {
            "record_hash": record_hash,
            "status": "error",
            "reason": f"{type(exc).__name__}: {exc}",
        }

    return {
        "record_hash": record_hash,
        "status": "ok" if ok else "verify_failed",
        "signature_status": sig_status,
        "signing_key_source": art_dict.get("signing_key_source"),
    }


@app.post("/counterfactual/replay/bulk")
async def replay_bulk(req: BulkReplayRequest) -> StreamingResponse:
    """Stream verify results for a batch of artifact hashes (NDJSON).

    Designed for periodic audit-chain integrity sweeps: an auditor
    submits the hashes they want re-verified and the engine streams
    one JSON object per line as each hash finishes. Failure modes
    (not_found / unsigned / verify_failed / error) are reported in
    the per-line ``status`` field, never as HTTP errors — the stream
    must keep flowing past individual failures so a long sweep
    produces a usable per-hash report.

    NDJSON instead of one big JSON list because:
      * The auditor's tooling can start consuming the first result
        before the last hash has been verified.
      * Partial output is still parseable if the connection drops.
      * Memory footprint is O(1) on the server side — no full-list
        accumulation required.

    Deduplicates input hashes before scheduling so an auditor pasting
    a CSV with duplicate rows doesn't re-verify the same artifact
    twice. Order is preserved (first occurrence wins).
    """
    seen: set = set()
    ordered_unique: List[str] = []
    for h in req.hashes:
        if h not in seen:
            seen.add(h)
            ordered_unique.append(h)

    async def _stream():
        # Each verify is sub-millisecond (signature check + canonical
        # dumps) but persistence read is filesystem-bound. Run them
        # one at a time — the bottleneck is disk, not CPU, and
        # streaming results keeps the latency-to-first-byte low.
        for h in ordered_unique:
            try:
                result = _verify_one_artifact(h)
            except Exception as exc:
                # Defensive: _verify_one_artifact is already supposed
                # to swallow all failures, but a totally unexpected
                # crash should still produce a structured row rather
                # than break the stream.
                logger.exception("Unexpected bulk-replay error for %s", h)
                result = {
                    "record_hash": h,
                    "status": "error",
                    "reason": f"{type(exc).__name__}: {exc}",
                }
            yield json.dumps(result) + "\n"

    return StreamingResponse(_stream(), media_type="application/x-ndjson")
