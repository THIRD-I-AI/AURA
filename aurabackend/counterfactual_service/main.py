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
import os
import pathlib
import re
import uuid
from typing import Any, Dict, List, Literal, Optional

import pandas as pd
from fastapi import Depends, HTTPException
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field

from shared.auth import require_user
from shared.exceptions import ForbiddenError
from shared.service_factory import create_service

from . import cryptography, pdf_renderer, persistence, signing
from .audit_worker import get_audit_pool, run_audit_subprocess
from .demo_scenarios import get_scenario, list_scenarios
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

# S31b: the demo runs a curated set of fast, modern estimators. The classical
# DoWhy backdoor methods (linear_regression/ipw/psm) bootstrap their CIs and
# take 15-30s each, while DR-Learner, TMLE and IV run in <1s and are the more
# credible methods anyway. IV (officer-leniency instrument) is what corrects
# the unobserved-confounding bias the backdoor methods can't see.
_DEMO_METHODS = ["double_ml", "tmle", "iv"]
_demo_last_good: Dict[str, Dict[str, Any]] = {}  # scenario_id → artifact dict
_prewarm_tasks: set = set()

# Demo artifacts are pre-computed by a separate one-shot process
# (`python -m counterfactual_service.warm_demos`) and persisted here, so the
# gateway — which mounts this service IN-PROCESS — can serve the instant /demo
# path by LOADING the JSON at startup rather than running a GIL-bound dowhy
# audit that would starve its event loop. See warm_demos.py.
_DEMO_ARTIFACT_DIR = pathlib.Path(
    os.getenv("AURA_DEMO_ARTIFACT_DIR", "data/demo_artifacts")
)


def _persist_demo_artifact(scenario_id: str, artifact_dict: Dict[str, Any]) -> None:
    try:
        _DEMO_ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
        tmp = _DEMO_ARTIFACT_DIR / f".{scenario_id}.json.tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(artifact_dict, fh)
        os.replace(tmp, _DEMO_ARTIFACT_DIR / f"{scenario_id}.json")
    except OSError as exc:
        logger.warning("could not persist demo artifact %s: %s", scenario_id, exc)


def load_persisted_demo_artifacts() -> int:
    """Load pre-computed demo artifacts from disk into the in-memory cache.

    Instant + CPU-free — safe to call from the gateway's startup. Returns the
    number of scenarios loaded. Missing dir / files is fine (returns 0): the
    deploy just hasn't run warm_demos yet."""
    n = 0
    if not _DEMO_ARTIFACT_DIR.exists():
        return 0
    for p in _DEMO_ARTIFACT_DIR.glob("*.json"):
        try:
            with open(p, encoding="utf-8") as fh:
                _demo_last_good[p.stem] = json.load(fh)
            n += 1
        except (OSError, ValueError) as exc:
            logger.warning("could not load demo artifact %s: %s", p.name, exc)
    if n:
        logger.info("loaded %d pre-computed demo artifact(s) from %s", n, _DEMO_ARTIFACT_DIR)
    return n


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
        # Defense-in-depth: the worker reaches this directly, so re-validate the
        # name against path traversal here too (not only at the HTTP boundary).
        if not _safe_upload_name(name):
            raise HTTPException(404, f"invalid uploaded file name: {name!r}")
        for d in (
            pathlib.Path("data/uploads"),
            pathlib.Path("api_gateway/uploads"),
            pathlib.Path("uploads"),
        ):
            base = d.resolve()
            p = (base / name).resolve()
            try:
                p.relative_to(base)
            except ValueError:
                continue
            if p.is_file() and p.suffix.lower() in _READ_FN_BY_EXT:
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


async def _run_demo_async(job_id: str, scenario_id: str, query: CounterfactualQuery) -> None:
    """Demo job worker: runs all 7 estimators; on failure serves the last
    good artifact (degraded) so the demo never shows a broken state."""
    _jobs[job_id]["state"] = "running"
    try:
        df = _resolve_dataset(query.dataset.source_id)
        artifact = await run_job(query, df=df, methods=_DEMO_METHODS)
        artifact.rendered = render(artifact, query.audience)
        art_dict = artifact.model_dump(mode="json")
        _demo_last_good[scenario_id] = art_dict
        _jobs[job_id].update(state="succeeded", artifact=art_dict)
    except Exception as exc:
        logger.exception("Demo job %s failed", job_id)
        fallback = _demo_last_good.get(scenario_id)
        if fallback is not None:
            patched = dict(fallback)
            patched["degraded"] = True
            _jobs[job_id].update(state="succeeded", artifact=patched)
        else:
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
        "estimators": ["linear_regression", "ipw", "psm", "double_ml", "forest_dr", "tmle", "iv"],
        "refuters":   ["random_common_cause", "placebo", "data_subset", "sensitivity"],
        "audiences":  ["operator", "auditor", "analyst"],
    }


# ── S31b — One-click demo on pre-loaded compliance data ───────────────

@app.get("/counterfactual/demo/scenarios")
async def demo_scenarios() -> Dict[str, Any]:
    """List the pre-loaded compliance audit scenarios available to /demo."""
    return {"scenarios": list_scenarios()}


@app.post("/counterfactual/demo/{scenario_id}")
async def run_demo(scenario_id: str, fresh: bool = False) -> Dict[str, Any]:
    """Run the pre-loaded compliance audit. Poll GET /counterfactual/jobs/{id}.

    Fast path: once a scenario is pre-warmed, return its sealed artifact as an
    already-complete job (instant, deterministic). ``fresh=true`` forces a live
    run for the "watch it compute" progress view."""
    try:
        scenario = get_scenario(scenario_id)
    except KeyError:
        raise HTTPException(404, f"unknown demo scenario: {scenario_id!r}")

    cached = _demo_last_good.get(scenario_id)
    if cached is not None and not fresh:
        job_id = f"demo_{uuid.uuid4().hex[:12]}"
        _jobs[job_id] = {"state": "succeeded", "artifact": cached, "error": None}
        return {"job_id": job_id, "scenario_id": scenario_id, "degraded": False, "cached": True}

    if not fresh:
        # No pre-warmed artifact and the caller didn't explicitly opt into a
        # live run. Refuse rather than launch a GIL-bound dowhy audit in-
        # request: in the gateway's in-process mount that would starve the
        # event loop and freeze every other request. Pre-warm out-of-process.
        raise HTTPException(
            503,
            f"demo scenario {scenario_id!r} is not pre-warmed. Run "
            f"`python -m counterfactual_service.warm_demos`, or retry with "
            f"?fresh=true to run a live audit (only safe when this service "
            f"runs as its own process).",
        )

    df = scenario.build_dataset()
    query = scenario.query()
    register_dataset(query.dataset.source_id, df)
    job_id = f"demo_{uuid.uuid4().hex[:12]}"
    _jobs[job_id] = {"state": "queued", "artifact": None, "error": None}
    _jobs[job_id]["_task"] = asyncio.create_task(
        _run_demo_async(job_id, scenario_id, query)
    )
    return {"job_id": job_id, "scenario_id": scenario_id, "degraded": False, "cached": False}


# ── Audit Your Own Data — run the engine on a user's uploaded CSV ──────

class AuditRequest(BaseModel):
    uploaded_file: str
    treatment: str
    outcome: str
    confounders: List[str] = []
    instrument: Optional[str] = None
    # Subsystem C — audit-grade identity for the durable ledger. subject_id is
    # the caller-supplied stable id of what's audited (a model / decision cohort
    # / applicant); preparer_id is the accountable human (AS 1215).
    tenant_id: str = "default"
    subject_id: str = "default"
    subject_type: str = "decision_model"
    preparer_id: str = "system"


# ``uploaded_file`` is user-controlled. A name like "../../etc/passwd" must never
# escape the uploads dir — reject anything but a bare safe filename, and re-verify
# the resolved realpath is inside the allow-listed base.
_SAFE_UPLOAD_NAME = re.compile(r"^[A-Za-z0-9._-]+$")


def _safe_upload_name(name: str) -> bool:
    return bool(name) and name not in (".", "..") and bool(_SAFE_UPLOAD_NAME.match(name))


def _find_upload(name: str) -> Optional[pathlib.Path]:
    if not _safe_upload_name(name):
        return None
    for d in (pathlib.Path("data/uploads"), pathlib.Path("api_gateway/uploads"),
              pathlib.Path("uploads")):
        base = d.resolve()
        p = (base / name).resolve()
        try:
            p.relative_to(base)
        except ValueError:
            continue  # escaped the base — refuse
        if p.is_file():
            return p
    return None


def _csv_header_columns(path: pathlib.Path) -> list:
    return list(pd.read_csv(path, nrows=0).columns)


def _result_field(result: Any, key: str, default: Any = None) -> Any:
    """Read a field from the audit result whether it crossed the process pool
    as a dict or as a model object."""
    if isinstance(result, dict):
        return result.get(key, default)
    return getattr(result, key, default)


async def _append_fairness_audit_to_ledger(result: Any, payload: Dict[str, Any]) -> None:
    """Chain a completed causal-fairness audit into the durable, tamper-evident
    ledger — the fair-lending product's exam-ready trail. A ledger hiccup is
    logged loudly but never fails the audit; an unsigned result (no cert hash)
    is not chained (there is no cert to commit to)."""
    cert_hash = _result_field(result, "audit_record_hash")
    if not cert_hash:
        return
    fingerprint = _result_field(result, "dataset_fingerprint", "") or cert_hash
    from shared import audit_ledger
    try:
        await audit_ledger.append_audit(
            tenant_id=payload.get("tenant_id") or "default",
            kind="fairness_audit_completed",
            subject_id=payload.get("subject_id") or "default",
            subject_type=payload.get("subject_type") or "decision_model",
            preparer_id=payload.get("preparer_id") or "system",
            cert_hash=cert_hash, input_fingerprint=fingerprint,
            payload={"treatment": payload.get("treatment"), "outcome": payload.get("outcome"),
                     "signature_status": _result_field(result, "signature_status")})
    except Exception as exc:  # noqa: BLE001 — never fail the audit on a ledger hiccup
        logger.error("fairness audit ledger append failed for %s: %s", cert_hash, exc)


async def _run_audit_job_async(job_id: str, payload: Dict[str, Any]) -> None:
    _jobs[job_id]["state"] = "running"
    loop = asyncio.get_event_loop()
    try:
        result = await loop.run_in_executor(get_audit_pool(), run_audit_subprocess, payload)
        _jobs[job_id].update(state="succeeded", artifact=result)
        await _append_fairness_audit_to_ledger(result, payload)
    except Exception as exc:
        logger.exception("Audit job %s failed", job_id)
        _jobs[job_id].update(state="failed", error=f"{type(exc).__name__}: {exc}")


@app.post("/counterfactual/audit")
async def run_audit(req: AuditRequest) -> Dict[str, Any]:
    """Audit the user's own uploaded data. Cheap pre-validation here; the heavy,
    GIL-bound fan-out runs out-of-process so the gateway never blocks."""
    path = _find_upload(req.uploaded_file)
    if path is None:
        raise HTTPException(404, f"uploaded file not found: {req.uploaded_file!r}")
    if path.suffix.lower() == ".csv":
        header = _csv_header_columns(path)
        needed = [req.treatment, req.outcome, *req.confounders] + (
            [req.instrument] if req.instrument else [])
        missing = [c for c in needed if c not in header]
        if missing:
            raise HTTPException(400, f"columns not in file {req.uploaded_file!r}: {missing}")

    job_id = f"audit_{uuid.uuid4().hex[:12]}"
    _jobs[job_id] = {"state": "queued", "artifact": None, "error": None}
    _jobs[job_id]["_task"] = asyncio.create_task(
        _run_audit_job_async(job_id, req.model_dump())
    )
    return {"job_id": job_id}


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

@app.get("/jwks")
async def jwks_endpoint() -> Dict[str, Any]:
    """Returns the JSON Web Key Set (JWKS) for ED25519 public keys."""
    try:
        return cryptography.get_jwks()
    except Exception as e:
        logger.error(f"Error fetching JWKS: {e}")
        raise HTTPException(status_code=500, detail="Internal Server Error")

async def _require_admin(user: Dict[str, Any] = Depends(require_user)) -> Dict[str, Any]:
    """Admin gate for privileged mutations. Requires a valid bearer token whose
    claims carry role == "admin". Fails closed: without auth wired/token present,
    the route 401s rather than allowing an anonymous key revocation."""
    if (user or {}).get("role") != "admin":
        raise ForbiddenError("admin role required to revoke signing keys")
    return user


@app.post("/counterfactual/admin/revoke-key", dependencies=[Depends(_require_admin)])
async def revoke_key(kid: str) -> Dict[str, str]:
    """Soft revokes an ED25519 key. Admin-only (see _require_admin)."""
    cryptography.soft_revoke_key(kid)
    return {"status": "success", "revoked_kid": kid, "message": "Key has been soft revoked."}


# ── S34a — Signed Financial Audit ─────────────────────────────────────

class FinancialAuditRequest(BaseModel):
    tenant_id: str
    ledger: List[Dict[str, Any]] = Field(default_factory=list)
    purchase_orders: List[Dict[str, Any]] = Field(default_factory=list)
    invoices: List[Dict[str, Any]] = Field(default_factory=list)
    journal_entries: List[Dict[str, Any]] = Field(default_factory=list)
    historical_reports: List[Dict[str, Any]] = Field(default_factory=list)
    # S39 — optional forensic inputs. Goods receipts enable the AS-2201
    # three-way match; period_end enables AS-2401 cutoff testing.
    goods_receipts: List[Dict[str, Any]] = Field(default_factory=list)
    period_end: Optional[str] = None
    # Subsystem C — audit-grade identity. subject_id is the caller-supplied
    # stable id of what's audited (a model / decision cohort / applicant) so
    # repeated audits link in the ledger; preparer_id is the accountable
    # human (AS 1215). Default to a per-tenant subject + system preparer.
    subject_id: str = "default"
    subject_type: str = "dataset"
    preparer_id: str = "system"


@app.post("/audit/financial")
async def financial_audit(req: FinancialAuditRequest) -> Dict[str, Any]:
    """Run the PCAOB checks on a ledger, sign an AS-1215 completion document with
    the persistent ED25519 key, persist it to the hash-chained log, and return the
    signed report (PII redacted at egress) with an independent verify URL."""
    from agents.specialists.financial_auditor import FinancialAuditorAgent

    from .financial_report import (
        build_completion_document,
        client_view,
        dataset_fingerprint,
        sign_and_persist,
    )
    agent = FinancialAuditorAgent(tenant_id=req.tenant_id)
    result = await agent.run_full_audit(
        req.ledger, req.purchase_orders, req.invoices, req.journal_entries, req.historical_reports,
        goods_receipts=req.goods_receipts or None, period_end=req.period_end)
    # Bind EVERY audited input in the signed fingerprint (the three-way-match,
    # expectation, and cutoff inputs drive findings — they must be attested).
    fingerprint = dataset_fingerprint(
        req.ledger, req.purchase_orders, req.invoices, req.journal_entries,
        goods_receipts=req.goods_receipts, historical_reports=req.historical_reports,
        period_end=req.period_end)
    doc = build_completion_document(
        req.tenant_id, result["findings"], fingerprint, result["materiality_threshold"],
        subject_id=req.subject_id, subject_type=req.subject_type, preparer_id=req.preparer_id)
    stored = sign_and_persist(doc)

    # Always-on durable ledger: chain this signed audit into the tenant's
    # tamper-evident history (Subsystem C). Never let a ledger hiccup fail the
    # audit response, but surface it loudly — a missing chain link is reportable.
    from shared import audit_ledger
    try:
        await audit_ledger.append_audit(
            tenant_id=req.tenant_id, kind="financial_audit_completed",
            subject_id=req.subject_id, subject_type=req.subject_type, preparer_id=req.preparer_id,
            cert_hash=stored["record_hash"], input_fingerprint=fingerprint,
            payload={"n_findings": stored.get("n_findings"),
                     "signature_status": stored.get("signature_status"),
                     "materiality_threshold": result["materiality_threshold"]})
    except Exception as exc:                       # noqa: BLE001
        logger.error("audit ledger append failed for %s: %s", stored["record_hash"], exc)

    view = client_view(stored)
    view["verify_url"] = f"/audit/financial/verify/{stored['record_hash']}"
    return view


@app.get("/audit/financial/demo")
async def financial_audit_demo() -> Dict[str, Any]:
    """One-click forensic demo: audits a canned dataset engineered to trip
    every implemented technique — AS-2401 Benford / duplicate / round-dollar /
    period-end cutoff, AS-2201 two-way + three-way match + segregation of
    duties + approval authority, AS-2305 absolute + expectation deviation —
    and returns the same signed, independently-verifiable report as
    ``/audit/financial``."""
    from .forensic_demo import forensic_demo_dataset
    return await financial_audit(FinancialAuditRequest(**forensic_demo_dataset()))


@app.get("/audit/financial/verify/{record_hash}")
async def financial_audit_verify(record_hash: str) -> Dict[str, Any]:
    from .financial_report import verify_report
    return verify_report(record_hash)


# ── Subsystem C — durable audit ledger verification surface ──────────────
# Tenant-scoped: the chain, the Merkle inclusion proof, and a subject's audit
# history. These are the artifacts an exam / auditor / opposing expert reads.

@app.get("/audit/ledger/verify")
async def audit_ledger_verify(tenant_id: str) -> Dict[str, Any]:
    """Re-walk the tenant's hash chain; ``ok=false`` + the offending seqs if any
    record was inserted, deleted, reordered, or edited."""
    from shared import audit_ledger
    return await audit_ledger.verify_chain(tenant_id)


@app.get("/audit/ledger/proof/{cert_hash}")
async def audit_ledger_proof(cert_hash: str, tenant_id: str) -> Dict[str, Any]:
    """Merkle inclusion proof that ``cert_hash``'s audit is in the tenant's
    chain — independently verifiable against the returned root."""
    from shared import audit_ledger
    proof = await audit_ledger.inclusion_proof(tenant_id, cert_hash)
    if proof is None:
        raise HTTPException(status_code=404, detail="no ledger record certifies that hash")
    return proof


@app.get("/audit/ledger/subject/{subject_id}")
async def audit_ledger_subject_history(subject_id: str, tenant_id: str) -> Dict[str, Any]:
    """Ordered audit history for one subject — every audit of this model /
    cohort / applicant, with its preparer and signed cert."""
    from shared import audit_ledger
    records = await audit_ledger.subject_history(tenant_id, subject_id)
    return {
        "tenant_id": tenant_id, "subject_id": subject_id, "count": len(records),
        "audits": [
            {"seq": r.seq, "kind": r.kind, "subject_type": r.subject_type,
             "preparer_id": r.preparer_id, "reviewer_id": r.reviewer_id,
             "cert_hash": r.cert_hash, "input_fingerprint": r.input_fingerprint,
             "ts": r.ts, "record_hash": r.record_hash}
            for r in records
        ],
    }


# ── S34b — HITL exception queue ───────────────────────────────────────

class ExceptionDecisionRequest(BaseModel):
    # No identity field: ``human_auditor_id`` is bound to the verified
    # token's ``sub`` so the signed AS 1215 record attests to who decided,
    # not to a caller-supplied string.
    rationale: str = Field(..., min_length=1)
    approved: bool


async def _require_auditor(user: Dict[str, Any] = Depends(require_user)) -> Dict[str, Any]:
    """Gate for human audit decisions. Mirrors ``_require_admin``: valid
    bearer token whose claims carry role auditor or admin. Fails closed —
    without auth wired/token present the route 401s/403s rather than
    accepting an anonymous (or impersonated) AS 1215 override."""
    if (user or {}).get("role") not in ("auditor", "admin"):
        raise ForbiddenError("auditor or admin role required to record decisions")
    return user


@app.get("/audit/financial/{record_hash}/exceptions")
async def financial_audit_exceptions(record_hash: str) -> Dict[str, Any]:
    """Findings of a signed completion document still awaiting a human
    decision (PII redacted at egress)."""
    from . import exception_queue
    try:
        return exception_queue.pending_exceptions(record_hash)
    except (LookupError, ValueError) as exc:
        raise HTTPException(404, str(exc))


@app.post("/audit/financial/{record_hash}/exceptions/{finding_id}/decision")
async def financial_audit_decide(record_hash: str, finding_id: str,
                                 req: ExceptionDecisionRequest,
                                 user: Dict[str, Any] = Depends(_require_auditor),
                                 ) -> Dict[str, Any]:
    """Record a human approve/override: WORM ``audit_human_override`` entry
    plus a signed HumanOverrideRecord artifact (AS 1215 contradiction doc).
    The decider's identity comes from the verified token, never the body."""
    from . import exception_queue
    if not req.rationale.strip():
        raise HTTPException(422, "AS 1215 requires a non-blank rationale")
    try:
        stored = exception_queue.record_decision(
            record_hash, finding_id, user["sub"], req.rationale, req.approved)
    except exception_queue.AlreadyDecidedError as exc:
        raise HTTPException(409, str(exc))
    except (LookupError, ValueError) as exc:
        # ValueError here = non-hex record_hash from the URL → not found.
        raise HTTPException(404, str(exc))
    stored["verify_url"] = f"/audit/financial/verify/{stored['record_hash']}"
    return stored


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
        # Sec-3 #24: surface only the exception class name to the auditor
        # — interpolating str(exc) into the response leaks server-side
        # filesystem paths, SQL fragments, and stack-trace context.
        logger.exception("Bulk verify failed for %s", record_hash)
        return {
            "record_hash": record_hash,
            "status": "error",
            "reason": type(exc).__name__,
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
                # than break the stream. Sec-2 #24: surface only the
                # exception class name to the auditor — exception
                # messages can leak server-side filesystem paths.
                logger.exception("Unexpected bulk-replay error for %s", h)
                result = {
                    "record_hash": h,
                    "status": "error",
                    "reason": type(exc).__name__,
                }
            yield json.dumps(result) + "\n"

    return StreamingResponse(_stream(), media_type="application/x-ndjson")


# ── Sprint 19 — TRAIGA Federation: Merkle audit log endpoints ────────
#
# Two new endpoints let an external auditor verify any audit record's
# inclusion in AURA's chain WITHOUT trusting the engine. Walkthrough:
#
#   1. Auditor knows ``record_hash`` (e.g., from a prior replay call).
#   2. Auditor calls GET /counterfactual/audit/inclusion/{record_hash}
#      → receives {leaf_index, proof_hex, tree_size, root_hash_hex, day}.
#   3. Auditor calls GET /counterfactual/audit/sth?day=<day>
#      → receives {tree_size, root_hash_hex, timestamp_iso,
#                  signature_b64, signing_key_source}.
#   4. Auditor verifies signature against the engine's public ED25519
#      key (already exposed via GET /counterfactual/public-key).
#   5. Auditor rebuilds the root from (record_hash, proof) using the
#      RFC 6962 verify_inclusion algorithm in shared/merkle.py.
#   6. Auditor compares the rebuilt root to the STH's root_hash_hex.
#      Match → inclusion proven; mismatch → tampering detected.
#
# The auditor only needs Python's hashlib + ED25519 verification +
# the published STH + the inclusion proof — no AURA-specific tooling,
# no Postgres connection, no live engine. This is the cross-org
# verifiability contract from RFC 6962 § 2.1 applied to TRAIGA records.


class STHResponse(BaseModel):
    """Signed Tree Head for a UTC day of audit records.

    RFC 6962 § 3.5 STH shape, simplified for the JSON wire surface:
    no separate ``sha256_root_hash`` field (we hex-encode in
    ``root_hash_hex``); ``timestamp`` is RFC 3339 not POSIX ms; no
    log_id (single-log deployment). Verifiers reconstruct the
    canonical signed bytes via the documented stable-field
    concatenation below.
    """
    tree_size: int = Field(..., ge=0)
    root_hash_hex: str
    timestamp_iso: str
    day: str
    service_tag: str
    signature_b64: Optional[str] = None
    signature_status: Literal["signed", "unsigned"] = "unsigned"
    signing_key_source: Optional[str] = None
    canonical_signed_bytes_b64: Optional[str] = None


def _canonical_sth_bytes(
    tree_size: int, root_hash_hex: str, timestamp_iso: str,
    day: str, service_tag: str,
) -> bytes:
    """The exact bytes that get signed. Auditors reconstruct these
    independently to verify the signature.

    Format: a single canonical-JSON object with sorted keys + no
    whitespace. Mirrors the Sprint 13 ``strip_for_hashing`` design
    where the byte-identity of the signed payload is part of the
    contract — any field reordering breaks signature verification."""
    import json as _json
    return _json.dumps(
        {
            "day": day,
            "root_hash_hex": root_hash_hex,
            "service_tag": service_tag,
            "timestamp_iso": timestamp_iso,
            "tree_size": tree_size,
        },
        sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")


@app.get("/counterfactual/audit/sth", response_model=STHResponse)
async def get_sth(day: Optional[str] = None) -> STHResponse:
    """Return the Signed Tree Head for a UTC day of audit records.

    When ``day`` is omitted, defaults to today's UTC date. Day
    format is YYYYMMDD (matches the audit log's daily rotation
    filename convention).

    Returns 404 when the day has no audit records (the
    daily_merkle_root helper returns None). Returns 200 with
    ``signature_status="unsigned"`` when the deployment lacks
    ED25519 signing — the root_hash is still correct, just not
    cryptographically attestable.
    """
    import datetime as _dt

    from shared.audit_log import daily_merkle_root

    target_day = day or _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%d")
    merkle_info = daily_merkle_root(target_day)
    if merkle_info is None:
        raise HTTPException(
            status_code=404,
            detail=f"no audit records for day={target_day}",
        )

    timestamp_iso = _dt.datetime.now(_dt.timezone.utc).isoformat()
    canonical = _canonical_sth_bytes(
        tree_size=merkle_info["tree_size"],
        root_hash_hex=merkle_info["root_hash_hex"],
        timestamp_iso=timestamp_iso,
        day=merkle_info["day"],
        service_tag=merkle_info["service_tag"],
    )
    sig_b64 = signing.sign_bytes(canonical)
    import base64 as _b64
    return STHResponse(
        tree_size=merkle_info["tree_size"],
        root_hash_hex=merkle_info["root_hash_hex"],
        timestamp_iso=timestamp_iso,
        day=merkle_info["day"],
        service_tag=merkle_info["service_tag"],
        signature_b64=sig_b64,
        signature_status="signed" if sig_b64 else "unsigned",
        signing_key_source=signing.signing_key_source() if sig_b64 else None,
        canonical_signed_bytes_b64=_b64.b64encode(canonical).decode("ascii"),
    )


class InclusionProofResponse(BaseModel):
    record_hash: str
    day: str
    service_tag: str
    tree_size: int
    leaf_index: int
    proof_hex: List[str]
    root_hash_hex: str


@app.get(
    "/counterfactual/audit/inclusion/{record_hash}",
    response_model=InclusionProofResponse,
)
async def get_inclusion_proof(
    record_hash: str,
    day: Optional[str] = None,
) -> InclusionProofResponse:
    """Return the RFC 6962 Merkle inclusion proof for a single audit
    record. Auditor uses this + the day's STH to verify the record
    was sealed at time T without trusting the engine.

    ``day`` parameter optionally narrows the search to one day's
    bucket. When omitted, the helper walks the last 30 days. Records
    older than 30 days require an explicit ``day=YYYYMMDD`` query."""
    from shared.audit_log import inclusion_proof_for_record

    proof_info = inclusion_proof_for_record(record_hash, day=day)
    if proof_info is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"record_hash {record_hash} not found in the last 30 days "
                f"of audit log; pass ?day=YYYYMMDD to search older buckets"
            ),
        )
    return InclusionProofResponse(record_hash=record_hash, **proof_info)


# ── S31b — Startup pre-warm (non-blocking) ────────────────────────────

async def prewarm_demo_scenarios() -> None:
    """Run each registered scenario once, caching the sealed artifact for the
    instant /demo path + the fail-safe. Best-effort (failures logged, never
    fatal). Idempotent — safe to call from any app's startup."""
    for meta in list_scenarios():
        sid = meta["id"]
        try:
            scenario = get_scenario(sid)
            df = scenario.build_dataset()
            query = scenario.query()
            register_dataset(query.dataset.source_id, df)
            artifact = await run_job(query, df=df, methods=_DEMO_METHODS)
            art_dict = artifact.model_dump(mode="json")
            _demo_last_good[sid] = art_dict
            _persist_demo_artifact(sid, art_dict)
            logger.info("pre-warmed demo scenario %s", sid)
        except Exception as exc:
            logger.warning("pre-warm of scenario %s failed (non-fatal): %s", sid, exc)


def start_demo_prewarm() -> None:
    """Spawn the pre-warm as a held background task so it never blocks
    startup. Callable from the counterfactual service's own startup AND from
    the api_gateway lifespan (the gateway mounts this service in-process, so
    the @app.on_event hook below does NOT fire in gateway-only deployments)."""
    task = asyncio.create_task(prewarm_demo_scenarios())
    _prewarm_tasks.add(task)
    task.add_done_callback(_prewarm_tasks.discard)


@app.on_event("startup")
async def _prewarm_demos() -> None:
    start_demo_prewarm()
