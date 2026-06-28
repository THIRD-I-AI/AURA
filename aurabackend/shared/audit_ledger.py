"""
Durable Audit Ledger (Subsystem C)
==================================
A durable, tamper-evident, tenant-scoped append-only ledger. This is the
product's defensibility moat for the fair-lending wedge: the artifact a
regulator / auditor / opposing expert reviews. Each signed audit appends one
record that chains ``prev_hash -> record_hash`` *within its tenant*, so any
post-hoc insertion, deletion, reordering, or edit is detectable.

Distinct from ``shared/audit_log.py`` (the legacy JSONL LLM-prompt log): this
is the always-on, DB-backed, multi-replica-safe ledger for audit completions
and human sign-offs. It owns its own engine/models/session (mirrors the S50
``api_gateway/persistence.py`` pattern) so every service can append to it.

Multi-replica chaining
----------------------
A hash chain needs a total order per chain. Per-tenant chains are serialised by:
  1. an in-process ``asyncio.Lock`` per tenant (cheap, handles the common
     single-process + asyncio-concurrency case);
  2. a Postgres transaction-scoped advisory lock (``pg_advisory_xact_lock``)
     for cross-replica ordering;
  3. a ``UNIQUE(tenant_id, seq)`` constraint as the fail-closed correctness net
     — if anything ever races past the locks, the second insert is rejected and
     the append retries from a fresh tip read rather than silently forking.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Dict, List, Optional

from sqlalchemy import Column, Index, Integer, String, Text, UniqueConstraint, select, text
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

logger = logging.getLogger("aura.shared.audit_ledger")

_MAX_APPEND_RETRIES = 8


class Base(DeclarativeBase):
    """Own DeclarativeBase — the ledger schema evolves independently."""


class AuditLedgerRow(Base):
    __tablename__ = "audit_ledger"

    id = Column(Integer, primary_key=True, autoincrement=True)
    tenant_id = Column(String(64), nullable=False, index=True)
    seq = Column(Integer, nullable=False)              # per-tenant, monotonic, gap-free
    kind = Column(String(64), nullable=False)
    subject_id = Column(String(128), nullable=False)
    subject_type = Column(String(32), nullable=False)
    preparer_id = Column(String(128), nullable=False)
    reviewer_id = Column(String(128), nullable=True)
    decided_at = Column(String(64), nullable=True)
    cert_hash = Column(String(128), nullable=False)
    input_fingerprint = Column(String(128), nullable=False)
    payload_json = Column(Text, nullable=False, default="{}")
    prev_hash = Column(String(64), nullable=False, default="")
    record_hash = Column(String(64), nullable=False)
    ts = Column(String(64), nullable=False)

    __table_args__ = (
        UniqueConstraint("tenant_id", "seq", name="uq_audit_ledger_tenant_seq"),
        Index("ix_audit_ledger_tenant_subject", "tenant_id", "subject_id"),
    )


@dataclass
class LedgerRecord:
    tenant_id: str
    seq: int
    kind: str
    subject_id: str
    subject_type: str
    preparer_id: str
    reviewer_id: Optional[str]
    decided_at: Optional[str]
    cert_hash: str
    input_fingerprint: str
    payload: Dict[str, Any]
    prev_hash: str
    record_hash: str
    ts: str


# ── Engine / session (self-contained, lazy-init) ─────────────────────────

_engine: Optional[AsyncEngine] = None
_session_factory: Optional[async_sessionmaker[AsyncSession]] = None
_schema_initialized: bool = False
_tenant_locks: Dict[str, asyncio.Lock] = {}


def database_url() -> str:
    return os.getenv("AURA_LEDGER_DATABASE_URL", "sqlite+aiosqlite:///data/audit_ledger.db")


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        url = database_url()
        connect_args: Dict[str, Any] = {}
        if url.startswith("sqlite"):
            connect_args["check_same_thread"] = False
        _engine = create_async_engine(url, echo=False, future=True, connect_args=connect_args)
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(get_engine(), expire_on_commit=False)
    return _session_factory


async def init_database() -> None:
    global _schema_initialized
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    _schema_initialized = True


async def close_database() -> None:
    global _engine, _session_factory, _schema_initialized
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _session_factory = None
        _schema_initialized = False


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    global _schema_initialized
    if not _schema_initialized:
        await init_database()
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


# ── Hashing (stable, reproducible by any verifier) ───────────────────────

def _canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _stable_fields(*, ts, tenant_id, seq, kind, subject_id, subject_type, preparer_id,
                    reviewer_id, decided_at, cert_hash, input_fingerprint,
                    payload_json, prev_hash) -> Dict[str, Any]:
    return {
        "ts": ts, "tenant_id": tenant_id, "seq": seq, "kind": kind,
        "subject_id": subject_id, "subject_type": subject_type,
        "preparer_id": preparer_id, "reviewer_id": reviewer_id or "",
        "decided_at": decided_at or "", "cert_hash": cert_hash,
        "input_fingerprint": input_fingerprint, "payload": payload_json,
        "prev_hash": prev_hash,
    }


def _hash(stable: Dict[str, Any]) -> str:
    return hashlib.sha256(_canonical(stable).encode("utf-8")).hexdigest()


def _lock_for(tenant_id: str) -> asyncio.Lock:
    lock = _tenant_locks.get(tenant_id)
    if lock is None:
        lock = asyncio.Lock()
        _tenant_locks[tenant_id] = lock
    return lock


def _row_to_record(row: AuditLedgerRow) -> LedgerRecord:
    try:
        payload = json.loads(row.payload_json) if row.payload_json else {}
    except (json.JSONDecodeError, TypeError):
        payload = {}
    return LedgerRecord(
        tenant_id=row.tenant_id, seq=row.seq, kind=row.kind, subject_id=row.subject_id,
        subject_type=row.subject_type, preparer_id=row.preparer_id, reviewer_id=row.reviewer_id,
        decided_at=row.decided_at, cert_hash=row.cert_hash, input_fingerprint=row.input_fingerprint,
        payload=payload, prev_hash=row.prev_hash, record_hash=row.record_hash, ts=row.ts,
    )


_REQUIRED = ("tenant_id", "kind", "subject_id", "subject_type", "preparer_id",
             "cert_hash", "input_fingerprint")


async def append_audit(*, tenant_id: str, kind: str, subject_id: str, subject_type: str,
                       preparer_id: str, cert_hash: str, input_fingerprint: str,
                       payload: Optional[Dict[str, Any]] = None,
                       reviewer_id: Optional[str] = None,
                       decided_at: Optional[str] = None) -> LedgerRecord:
    """Append one chained record to ``tenant_id``'s ledger. Serialised per
    tenant; retries on the UNIQUE-constraint net. Raises ValueError if a
    required audit-grade field (incl. the preparer assignment) is missing."""
    args = dict(tenant_id=tenant_id, kind=kind, subject_id=subject_id,
                subject_type=subject_type, preparer_id=preparer_id,
                cert_hash=cert_hash, input_fingerprint=input_fingerprint)
    for name in _REQUIRED:
        if not args[name] or not str(args[name]).strip():
            raise ValueError(f"audit_ledger.append_audit: '{name}' is required")

    payload_json = _canonical(payload or {})
    last_exc: Optional[Exception] = None

    async with _lock_for(tenant_id):
        for _ in range(_MAX_APPEND_RETRIES):
            try:
                async with session_scope() as session:
                    if session.bind is not None and session.bind.dialect.name == "postgresql":
                        await session.execute(
                            text("SELECT pg_advisory_xact_lock(hashtext(:t))"), {"t": tenant_id})
                    tip = (await session.execute(
                        select(AuditLedgerRow)
                        .where(AuditLedgerRow.tenant_id == tenant_id)
                        .order_by(AuditLedgerRow.seq.desc())
                        .limit(1)
                    )).scalar_one_or_none()
                    seq = (tip.seq + 1) if tip else 1
                    prev_hash = tip.record_hash if tip else ""
                    ts = datetime.now(timezone.utc).isoformat()
                    stable = _stable_fields(
                        ts=ts, tenant_id=tenant_id, seq=seq, kind=kind, subject_id=subject_id,
                        subject_type=subject_type, preparer_id=preparer_id, reviewer_id=reviewer_id,
                        decided_at=decided_at, cert_hash=cert_hash,
                        input_fingerprint=input_fingerprint, payload_json=payload_json,
                        prev_hash=prev_hash,
                    )
                    record_hash = _hash(stable)
                    row = AuditLedgerRow(
                        tenant_id=tenant_id, seq=seq, kind=kind, subject_id=subject_id,
                        subject_type=subject_type, preparer_id=preparer_id, reviewer_id=reviewer_id,
                        decided_at=decided_at, cert_hash=cert_hash,
                        input_fingerprint=input_fingerprint, payload_json=payload_json,
                        prev_hash=prev_hash, record_hash=record_hash, ts=ts,
                    )
                    session.add(row)
                return _row_to_record(row)
            except (IntegrityError, OperationalError) as exc:
                last_exc = exc                          # UNIQUE(tenant,seq) race / lock contention
                continue
    raise RuntimeError(f"audit_ledger append failed after {_MAX_APPEND_RETRIES} retries: {last_exc}")


async def verify_chain(tenant_id: str) -> Dict[str, Any]:
    """Re-walk a tenant's chain: every record_hash must match its content, and
    its prev_hash must equal the *recomputed* hash of its predecessor — so any
    edit invalidates that record AND every successor."""
    async with session_scope() as session:
        rows = (await session.execute(
            select(AuditLedgerRow)
            .where(AuditLedgerRow.tenant_id == tenant_id)
            .order_by(AuditLedgerRow.seq.asc())
        )).scalars().all()

    prev = ""
    failures: List[Dict[str, Any]] = []
    for i, row in enumerate(rows):
        expected_seq = i + 1
        if row.seq != expected_seq:
            failures.append({"seq": row.seq, "error": f"seq gap (expected {expected_seq})"})
        stable = _stable_fields(
            ts=row.ts, tenant_id=row.tenant_id, seq=row.seq, kind=row.kind,
            subject_id=row.subject_id, subject_type=row.subject_type, preparer_id=row.preparer_id,
            reviewer_id=row.reviewer_id, decided_at=row.decided_at, cert_hash=row.cert_hash,
            input_fingerprint=row.input_fingerprint, payload_json=row.payload_json,
            prev_hash=row.prev_hash,
        )
        recomputed = _hash(stable)
        if recomputed != row.record_hash:
            failures.append({"seq": row.seq, "error": "record_hash mismatch"})
        if row.prev_hash != prev:
            failures.append({"seq": row.seq, "error": "prev_hash mismatch"})
        prev = recomputed                               # chain on the recomputed hash
    return {"tenant_id": tenant_id, "count": len(rows), "failures": failures, "ok": not failures}


async def subject_history(tenant_id: str, subject_id: str) -> List[LedgerRecord]:
    """Ordered audit history for one subject within a tenant."""
    async with session_scope() as session:
        rows = (await session.execute(
            select(AuditLedgerRow)
            .where(AuditLedgerRow.tenant_id == tenant_id, AuditLedgerRow.subject_id == subject_id)
            .order_by(AuditLedgerRow.seq.asc())
        )).scalars().all()
    return [_row_to_record(r) for r in rows]


# ── Merkle commitment over the durable store (RFC 6962) ──────────────────
# The root is the cryptographic anchor an external party can publish/pin; an
# auditor holding (record_hash, proof, root) verifies a record's inclusion in
# the tenant's chain without trusting AURA. Computed on demand from the DB
# (not per-append), so the hot path stays a single insert.

async def _tenant_record_hashes(tenant_id: str) -> List[str]:
    async with session_scope() as session:
        return list((await session.execute(
            select(AuditLedgerRow.record_hash)
            .where(AuditLedgerRow.tenant_id == tenant_id)
            .order_by(AuditLedgerRow.seq.asc())
        )).scalars().all())


async def merkle_root(tenant_id: str) -> Optional[Dict[str, Any]]:
    """Merkle Tree Hash over a tenant's record hashes, or None if empty."""
    from .merkle import build_tree_root, leaf_hash
    hashes = await _tenant_record_hashes(tenant_id)
    if not hashes:
        return None
    leaves = [leaf_hash(h.encode("utf-8")) for h in hashes]
    return {
        "tenant_id": tenant_id,
        "tree_size": len(hashes),
        "root_hash_hex": build_tree_root(leaves).hex(),
    }


async def inclusion_proof(tenant_id: str, cert_hash: str) -> Optional[Dict[str, Any]]:
    """Merkle inclusion proof for the ledger record that certified ``cert_hash``.

    Returns ``{tenant_id, tree_size, leaf_index, cert_hash, record_hash,
    proof_hex, root_hash_hex}`` (proof ordered leaf→root, the order
    ``merkle.verify_inclusion`` consumes), or None if no record certifies it."""
    from .merkle import build_tree_root, leaf_hash
    from .merkle import inclusion_proof as _mk_proof
    async with session_scope() as session:
        rows = (await session.execute(
            select(AuditLedgerRow)
            .where(AuditLedgerRow.tenant_id == tenant_id)
            .order_by(AuditLedgerRow.seq.asc())
        )).scalars().all()
    idx = next((i for i, r in enumerate(rows) if r.cert_hash == cert_hash), None)
    if idx is None:
        return None
    leaves = [leaf_hash(r.record_hash.encode("utf-8")) for r in rows]
    proof = _mk_proof(leaves, idx)
    root = build_tree_root(leaves)
    return {
        "tenant_id": tenant_id,
        "tree_size": len(rows),
        "leaf_index": idx,
        "cert_hash": cert_hash,
        "record_hash": rows[idx].record_hash,
        "proof_hex": [p.hex() for p in proof],
        "root_hash_hex": root.hex(),
    }


__all__ = [
    "AuditLedgerRow", "LedgerRecord", "append_audit", "verify_chain", "subject_history",
    "merkle_root", "inclusion_proof",
    "session_scope", "init_database", "close_database", "database_url",
]
