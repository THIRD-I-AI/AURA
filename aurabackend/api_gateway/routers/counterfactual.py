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
from fastapi.responses import Response, StreamingResponse

from counterfactual_service.main import (
    AuditRequest,
    BulkReplayRequest,
    ExceptionDecisionRequest,
    FinancialAuditRequest,
    InclusionProofResponse,
    STHResponse,
    _require_admin,
    _require_auditor,
)
from counterfactual_service.main import (
    audit_ledger_proof as _svc_audit_ledger_proof,
)
from counterfactual_service.main import (
    audit_ledger_subject_history as _svc_audit_ledger_subject_history,
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
    get_inclusion_proof as _svc_get_inclusion_proof,
)
from counterfactual_service.main import (
    get_job as _svc_get,
)
from counterfactual_service.main import (
    get_public_key as _svc_get_public_key,
)
from counterfactual_service.main import (
    get_sth as _svc_get_sth,
)
from counterfactual_service.main import (
    info as _svc_info,
)
from counterfactual_service.main import (
    jwks_endpoint as _svc_jwks_endpoint,
)
from counterfactual_service.main import (
    replay_bulk as _svc_replay_bulk,
)
from counterfactual_service.main import (
    revoke_key as _svc_revoke_key,
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


@router.get("/jwks")
async def jwks() -> Dict[str, Any]:
    """JWKS surface an external auditor's tooling fetches to verify ED25519
    signatures without any AURA-specific client. Service route is unauthed
    (public key material), so this proxy takes no auth either."""
    return await _svc_jwks_endpoint()


@router.post("/admin/revoke-key")
async def admin_revoke_key(
    kid: str, user: Dict[str, Any] = Depends(_require_admin),
) -> Dict[str, str]:
    return await _svc_revoke_key(kid)


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


@router.get("/audit/ledger/proof/{cert_hash}")
async def audit_ledger_proof(cert_hash: str, tenant: str = Depends(require_tenant)) -> Dict[str, Any]:
    """RFC 6962 Merkle inclusion proof for one cert within the tenant's chain.
    The service function takes tenant_id as a plain arg because it's an
    internal helper; the GATEWAY is the trust boundary, so — like
    audit_ledger_verify above, same underlying tenant-scoped AuditLedgerRow
    table — tenant comes from the verified JWT, never a caller-supplied
    query param (which would let any org read another org's proof by just
    passing a different tenant_id)."""
    return await _svc_audit_ledger_proof(cert_hash, tenant)


@router.get("/audit/ledger/subject/{subject_id}")
async def audit_ledger_subject_history(subject_id: str, tenant: str = Depends(require_tenant)) -> Dict[str, Any]:
    """Same require_tenant rationale as audit_ledger_proof above — this is
    the MORE sensitive of the two (a subject's full audit history), so it
    gets the same JWT-derived tenant, not a caller-supplied one."""
    return await _svc_audit_ledger_subject_history(subject_id, tenant)


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


# ── Sprint 13 — Auditor bulk-replay ───────────────────────────────────
# No tenant scoping here, matching /artifacts/{record_hash} and
# /artifacts/{record_hash}/verify above (also unauthenticated as far as
# TENANT goes): the counterfactual artifact store
# (counterfactual_service/persistence.py) is NOT tenant-scoped storage —
# it's a single content-addressed directory keyed only by record_hash, a
# 256-bit hex hash with no tenant column anywhere in that layer. There is
# no tenant_id parameter here to leak because the underlying store doesn't
# partition by tenant.
#
# It DOES need an auth gate, though — unlike the single-hash GET routes
# above, this is unauthenticated batch surface: the per-line "status" field
# (not_found vs unsigned vs verify_failed) turns it into an existence
# oracle for up to 256 caller-supplied hashes per call, and it's a cheap
# DoS lever (256 signature verifications from one anonymous request). The
# service function itself takes no user param — it was never auth-gated
# anywhere — so this is the ONE new route in this file where the gateway
# adds a check the service doesn't declare, matching its own docstring's
# design intent ("an auditor submits the hashes they want re-verified").
# `user` is intentionally unused below: _require_auditor is wired purely to
# enforce the role check, not to pass data into the service call.
# Residual gap (tracked, not fixed here): an authenticated auditor from one
# tenant can still submit a hash belonging to another tenant's artifact and
# learn whether it verifies — the artifact store has no per-tenant
# partition to check against. Follow-up decision, not this fix.

@router.post("/replay/bulk")
async def replay_bulk(
    req: BulkReplayRequest, user: Dict[str, Any] = Depends(_require_auditor),
) -> StreamingResponse:
    return await _svc_replay_bulk(req)


# ── Sprint 19 — TRAIGA Federation: Merkle audit log endpoints ─────────
# Also no tenant scoping, for a different reason than replay/bulk above:
# these read shared.audit_log's daily JSONL (a SEPARATE system from the
# per-tenant DB-backed shared.audit_ledger used by /audit/ledger/* above).
# Neither the service functions nor daily_merkle_root/inclusion_proof_for_
# record accept a tenant parameter at all — the signed tree head covers
# ALL records for a UTC day as one global RFC 6962 transparency log by
# design (see the Sprint 19 module comment below), so there is no
# caller-suppliable tenant identifier here to secure.
#
# Return types are the service's own Pydantic response models (not
# Dict[str, Any] like the routes above): these handlers return model
# INSTANCES straight from the service function, and FastAPI derives its
# response validation from THIS function's own return annotation (the
# service's response_model plays no role in an in-process call). Annotating
# these as Dict[str, Any] would make FastAPI try to validate a BaseModel
# instance against a dict schema and 500.

@router.get("/audit/sth")
async def get_sth(day: Optional[str] = None) -> STHResponse:
    return await _svc_get_sth(day=day)


@router.get("/audit/inclusion/{record_hash}")
async def get_inclusion_proof(record_hash: str, day: Optional[str] = None) -> InclusionProofResponse:
    return await _svc_get_inclusion_proof(record_hash, day=day)
