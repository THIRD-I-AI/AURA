"""N-of-M signed approval chains — collaboration v1 (enterprise roadmap #2).

Multiple humans authorize one action (pipeline deploy, healing shim, audit
sign-off). Rules, all fail-closed:

- the chain completes only when ``required_approvals`` DISTINCT approvers approve;
- any single reject resolves the chain as rejected;
- an approver votes at most once;
- the requester cannot vote on their own request (segregation of duties, AS-2201);
- resolved chains accept no further votes;
- lookups are tenant-scoped — a request id from another tenant is "not found".

Every request and every vote is appended to the tamper-evident per-tenant
audit ledger (shared/audit_ledger.py), so an examiner can replay exactly who
authorized what, in what order, with chain integrity guarantees. Storage
shares the ledger's engine/Base: approvals ARE audit artifacts.

Single-replica note: vote serialisation reuses the ledger's per-tenant
asyncio lock; multi-replica deployments get correctness from the ledger's
Postgres advisory lock on append, but request-status races across replicas
are a documented follow-up (row-level SELECT ... FOR UPDATE).
"""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List

from sqlalchemy import Integer, String, Text, select
from sqlalchemy.orm import Mapped, mapped_column

from shared import audit_ledger
from shared.audit_ledger import Base


class ApprovalRequestRow(Base):
    __tablename__ = "approval_requests"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(255), index=True, nullable=False)
    action_kind: Mapped[str] = mapped_column(String(128), nullable=False)
    subject_id: Mapped[str] = mapped_column(String(255), nullable=False)
    required_approvals: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    created_by: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[str] = mapped_column(String(64), nullable=False)
    decisions_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _decision_hash(request_id: str, approver: str, approve: bool, ts: str) -> str:
    return hashlib.sha256(
        json.dumps([request_id, approver, approve, ts], separators=(",", ":")).encode()
    ).hexdigest()


def _to_dict(row: ApprovalRequestRow) -> Dict[str, Any]:
    decisions = json.loads(row.decisions_json)
    return {
        "id": row.id,
        "tenant_id": row.tenant_id,
        "action_kind": row.action_kind,
        "subject_id": row.subject_id,
        "required_approvals": row.required_approvals,
        "status": row.status,
        "created_by": row.created_by,
        "created_at": row.created_at,
        "decisions": decisions,
        "approvals": sum(1 for d in decisions if d["approve"]),
    }


async def create_request(*, tenant_id: str, action_kind: str, subject_id: str,
                         required_approvals: int, created_by: str) -> Dict[str, Any]:
    if required_approvals < 1:
        raise ValueError("required_approvals must be >= 1")
    await audit_ledger.init_database()
    row = ApprovalRequestRow(
        id=f"apr_{uuid.uuid4().hex[:16]}", tenant_id=tenant_id, action_kind=action_kind,
        subject_id=subject_id, required_approvals=required_approvals,
        status="pending", created_by=created_by, created_at=_now(), decisions_json="[]")
    async with audit_ledger.session_scope() as session:
        session.add(row)
        await session.commit()
        await session.refresh(row)
    await audit_ledger.append_audit(
        tenant_id=tenant_id, kind="approval_requested", subject_id=subject_id,
        subject_type=action_kind, preparer_id=created_by,
        cert_hash=_decision_hash(row.id, created_by, True, row.created_at),
        input_fingerprint=row.id.ljust(64, "0"),
        payload={"request_id": row.id, "required_approvals": required_approvals})
    return _to_dict(row)


async def _get_row(session, request_id: str, tenant_id: str) -> ApprovalRequestRow:
    row = (await session.execute(
        select(ApprovalRequestRow).where(
            ApprovalRequestRow.id == request_id,
            ApprovalRequestRow.tenant_id == tenant_id))).scalar_one_or_none()
    if row is None:
        raise LookupError(f"approval request {request_id!r} not found for this tenant")
    return row


async def decide(*, request_id: str, tenant_id: str, approver: str, approve: bool) -> Dict[str, Any]:
    """Record one signed vote. Serialised per tenant; every vote lands in the
    audit ledger as part of the transition."""
    await audit_ledger.init_database()
    async with audit_ledger._lock_for(tenant_id):
        async with audit_ledger.session_scope() as session:
            row = await _get_row(session, request_id, tenant_id)
            if row.status != "pending":
                raise ValueError(f"approval request is resolved ({row.status}) — no further votes")
            if approver == row.created_by:
                raise ValueError("segregation of duties: the requester cannot vote on their own request")
            decisions: List[Dict[str, Any]] = json.loads(row.decisions_json)
            if any(d["approver"] == approver for d in decisions):
                raise ValueError(f"approver {approver!r} already decided on this request")

            ts = _now()
            decisions.append({"approver": approver, "approve": approve, "ts": ts})
            approvals = sum(1 for d in decisions if d["approve"])
            if not approve:
                row.status = "rejected"                      # any reject fails closed
            elif approvals >= row.required_approvals:
                row.status = "approved"
            row.decisions_json = json.dumps(decisions)
            await session.commit()
            await session.refresh(row)
            result = _to_dict(row)

    await audit_ledger.append_audit(
        tenant_id=tenant_id, kind="approval_decision", subject_id=result["subject_id"],
        subject_type=result["action_kind"], preparer_id=result["created_by"],
        reviewer_id=approver, decided_at=ts,
        cert_hash=_decision_hash(request_id, approver, approve, ts),
        input_fingerprint=request_id.ljust(64, "0"),
        payload={"request_id": request_id, "approve": approve,
                 "status_after": result["status"], "approvals": result["approvals"]})
    return result


async def pending(tenant_id: str) -> List[Dict[str, Any]]:
    await audit_ledger.init_database()
    async with audit_ledger.session_scope() as session:
        rows = (await session.execute(
            select(ApprovalRequestRow).where(
                ApprovalRequestRow.tenant_id == tenant_id,
                ApprovalRequestRow.status == "pending",
            ).order_by(ApprovalRequestRow.created_at))).scalars().all()
        return [_to_dict(r) for r in rows]


async def get_request(request_id: str, tenant_id: str) -> Dict[str, Any]:
    await audit_ledger.init_database()
    async with audit_ledger.session_scope() as session:
        return _to_dict(await _get_row(session, request_id, tenant_id))
