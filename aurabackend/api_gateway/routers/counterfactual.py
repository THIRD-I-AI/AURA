"""
Chat-facing Counterfactual Audit Engine router.

In v1 we mount the ``counterfactual_service`` endpoints in-process
under ``/api/v1/counterfactual/`` instead of doing an httpx-proxied HTTP
hop. The wire format matches the standalone service so when Sprint 9
splits the service into its own pod we just swap the in-process call
for an httpx client without touching front-end code.
"""
from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter
from fastapi.responses import Response

from counterfactual_service.main import (
    AuditRequest,
)
from counterfactual_service.main import (
    FinancialAuditRequest,
)
from counterfactual_service.main import (
    financial_audit as _svc_financial_audit,
)
from counterfactual_service.main import (
    financial_audit_verify as _svc_financial_audit_verify,
)
from counterfactual_service.main import (
    demo_scenarios as _svc_demo_scenarios,
)
from counterfactual_service.main import (
    get_artifact as _svc_get_artifact,
)
from counterfactual_service.main import (
    get_artifact_pdf as _svc_get_artifact_pdf,
)
from counterfactual_service.main import (
    get_job as _svc_get,
)
from counterfactual_service.main import (
    get_public_key as _svc_get_public_key,
)
from counterfactual_service.main import (
    info as _svc_info,
)
from counterfactual_service.main import (
    run_audit as _svc_run_audit,
)
from counterfactual_service.main import (
    run_demo as _svc_run_demo,
)
from counterfactual_service.main import (
    submit_job as _svc_submit,
)
from counterfactual_service.main import (
    verify_artifact as _svc_verify_artifact,
)
from counterfactual_service.schemas import CounterfactualQuery

router = APIRouter(prefix="/counterfactual", tags=["counterfactual"])


@router.post("/jobs")
async def submit(query: CounterfactualQuery) -> Dict[str, Any]:
    return await _svc_submit(query)


@router.get("/jobs/{job_id}")
async def status(job_id: str) -> Dict[str, Any]:
    return await _svc_get(job_id)


@router.get("/info")
async def info() -> Dict[str, Any]:
    return await _svc_info()


# ── Sprint 9 — Auditor view ───────────────────────────────────────────

@router.get("/artifacts/{record_hash}")
async def replay_artifact(record_hash: str) -> Dict[str, Any]:
    return await _svc_get_artifact(record_hash)


@router.get("/artifacts/{record_hash}/report.pdf")
async def report_pdf(record_hash: str) -> Response:
    return await _svc_get_artifact_pdf(record_hash)


@router.get("/artifacts/{record_hash}/verify")
async def verify_artifact(record_hash: str) -> Dict[str, Any]:
    return await _svc_verify_artifact(record_hash)


@router.get("/public-key")
async def public_key() -> Dict[str, Any]:
    return await _svc_get_public_key()


# ── S31b — One-click demo on pre-loaded compliance data ───────────────

@router.get("/demo/scenarios")
async def demo_scenarios() -> Dict[str, Any]:
    return await _svc_demo_scenarios()


@router.post("/demo/{scenario_id}")
async def run_demo(scenario_id: str, fresh: bool = False) -> Dict[str, Any]:
    return await _svc_run_demo(scenario_id, fresh=fresh)


@router.post("/audit")
async def run_audit(req: AuditRequest) -> Dict[str, Any]:
    return await _svc_run_audit(req)


@router.post("/audit/financial")
async def financial_audit(req: FinancialAuditRequest) -> Dict[str, Any]:
    return await _svc_financial_audit(req)


@router.get("/audit/financial/verify/{record_hash}")
async def financial_audit_verify(record_hash: str) -> Dict[str, Any]:
    return await _svc_financial_audit_verify(record_hash)
