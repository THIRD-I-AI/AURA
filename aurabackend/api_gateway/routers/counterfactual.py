"""
Chat-facing Counterfactual Audit Engine router.

In v1 we mount the ``counterfactual_service`` endpoints in-process
under ``/api/v1/counterfactual/`` instead of doing an httpx-proxied HTTP
hop. The wire format matches the standalone service so when Sprint 9
splits the service into its own pod we just swap the in-process call
for an httpx client without touching front-end code.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends
from fastapi.responses import Response

from counterfactual_service.main import (
    AuditRequest,
    ExceptionDecisionRequest,
    FinancialAuditRequest,
    _require_auditor,
)
from counterfactual_service.main import (
    audit_ledger_verify as _svc_audit_ledger_verify,
)
from counterfactual_service.main import (
    demo_scenarios as _svc_demo_scenarios,
)
from counterfactual_service.main import (
    financial_audit as _svc_financial_audit,
)
from counterfactual_service.main import (
    financial_audit_decide as _svc_financial_audit_decide,
)
from counterfactual_service.main import (
    financial_audit_demo as _svc_financial_audit_demo,
)
from counterfactual_service.main import (
    financial_audit_exceptions as _svc_financial_audit_exceptions,
)
from counterfactual_service.main import (
    financial_audit_verify as _svc_financial_audit_verify,
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
from shared.auth import get_current_user, require_tenant, require_user

router = APIRouter(prefix="/counterfactual", tags=["counterfactual"])

# These handlers call the service functions DIRECTLY (in-process mount, see the
# module docstring), so FastAPI never resolves the service function's own
# Depends() defaults — they would arrive as raw Depends sentinels. Every
# dependency the service needs must therefore be declared here and forwarded by
# hand. Jobs are tenant-scoped (counterfactual_service.main._new_job) → require_user.


@router.post("/jobs")
async def submit(query: CounterfactualQuery,
                 user: Dict[str, Any] = Depends(require_user)) -> Dict[str, Any]:
    return await _svc_submit(query, user=user)


@router.get("/jobs/{job_id}")
async def status(job_id: str,
                 user: Dict[str, Any] = Depends(require_user)) -> Dict[str, Any]:
    return await _svc_get(job_id, user=user)


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
async def run_demo(scenario_id: str, fresh: bool = False,
                   user: Dict[str, Any] = Depends(require_user)) -> Dict[str, Any]:
    return await _svc_run_demo(scenario_id, fresh=fresh, user=user)


@router.post("/audit")
async def run_audit(req: AuditRequest,
                    user: Dict[str, Any] = Depends(require_user)) -> Dict[str, Any]:
    return await _svc_run_audit(req, user=user)


@router.post("/audit/financial")
async def financial_audit(req: FinancialAuditRequest,
                         user: Optional[Dict[str, Any]] = Depends(get_current_user),
                         ) -> Dict[str, Any]:
    # Was calling the service bare, so its own Depends(get_current_user) default
    # arrived as a Depends sentinel and _ledger_tenant() crashed on .get() —
    # this route 500'd through the gateway while working service-direct.
    # NOTE: still OPTIONAL auth, matching the service. An anonymous call lands
    # in the "default" tenant chain; tightening it to require_user is a posture
    # decision, not a bug fix, so it is deliberately left alone here.
    return await _svc_financial_audit(req, user=user)


@router.get("/audit/financial/demo")
async def financial_audit_demo() -> Dict[str, Any]:
    """S40 one-click forensic demo — the cockpit's 'Run signed audit'. The
    facade previously never exposed it, so the Workbench 404'd through the
    gateway while the service route worked (browser-verified gap)."""
    return await _svc_financial_audit_demo()


@router.get("/audit/ledger/verify")
async def audit_ledger_verify(tenant: str = Depends(require_tenant)) -> Dict[str, Any]:
    """Tenant hash-chain verification for the cockpit's ledger chip. Tenant
    comes from the verified JWT (require_tenant), never a caller header."""
    return await _svc_audit_ledger_verify(tenant)


@router.get("/audit/financial/verify/{record_hash}")
async def financial_audit_verify(record_hash: str) -> Dict[str, Any]:
    return await _svc_financial_audit_verify(record_hash)


@router.get("/audit/financial/{record_hash}/exceptions")
async def financial_audit_exceptions(record_hash: str) -> Dict[str, Any]:
    return await _svc_financial_audit_exceptions(record_hash)


@router.post("/audit/financial/{record_hash}/exceptions/{finding_id}/decision")
async def financial_audit_decide(
    record_hash: str, finding_id: str, req: ExceptionDecisionRequest,
    user: Dict[str, Any] = Depends(_require_auditor),
) -> Dict[str, Any]:
    return await _svc_financial_audit_decide(record_hash, finding_id, req, user=user)
