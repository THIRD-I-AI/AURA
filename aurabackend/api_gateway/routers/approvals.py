"""
Approvals Router — ``/approvals``
=================================
N-of-M signed approval chains (collaboration v1). Identity comes from the
verified token, never the body; the tenant scope comes from ``require_tenant``.
Every request and vote is chained into the tamper-evident audit ledger —
see shared/approval_chains.py for the fail-closed rules.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from shared.auth import require_tenant, require_user
from shared.exceptions import ConflictError, NotFoundError, ValidationError

router = APIRouter(prefix="/approvals", tags=["approvals"])


class CreateApprovalRequest(BaseModel):
    action_kind: str = Field(..., min_length=1, max_length=128)
    subject_id: str = Field(..., min_length=1, max_length=255)
    required_approvals: int = Field(..., ge=1, le=25)


class DecideRequest(BaseModel):
    approve: bool


@router.post("", status_code=201)
async def create_approval(body: CreateApprovalRequest,
                          user: dict = Depends(require_user),
                          tenant: str = Depends(require_tenant)):
    from shared import approval_chains
    try:
        return await approval_chains.create_request(
            tenant_id=tenant, action_kind=body.action_kind, subject_id=body.subject_id,
            required_approvals=body.required_approvals, created_by=user["sub"])
    except ValueError as exc:
        raise ValidationError(str(exc))


@router.get("/pending")
async def list_pending(tenant: str = Depends(require_tenant)):
    from shared import approval_chains
    return {"pending": await approval_chains.pending(tenant)}


@router.get("/{request_id}")
async def get_approval(request_id: str, tenant: str = Depends(require_tenant)):
    from shared import approval_chains
    try:
        return await approval_chains.get_request(request_id, tenant)
    except LookupError:
        raise NotFoundError("approval request", request_id)


@router.post("/{request_id}/decide")
async def decide_approval(request_id: str, body: DecideRequest,
                          user: dict = Depends(require_user),
                          tenant: str = Depends(require_tenant)):
    from shared import approval_chains
    try:
        return await approval_chains.decide(
            request_id=request_id, tenant_id=tenant, approver=user["sub"], approve=body.approve)
    except LookupError:
        raise NotFoundError("approval request", request_id)
    except ValueError as exc:
        # duplicate vote / SoD / resolved chain — a conflict, not a validation error
        raise ConflictError(str(exc))
