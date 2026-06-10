"""Signed financial-audit report: fingerprint, AS-1215 completion document,
sign+persist, verify, and the egress (PII-redacted) client view.

Reuses signing.py (persistent ED25519), persistence.py (artifact store),
canonical.py (byte-stable JSON), audit_log.py (hash-chained WORM log)."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Dict, List

from agents.specialists.financial_auditor import FINANCIAL_AUDITOR_VERSION
from shared.audit_log import audit_event

from . import cryptography, persistence, signing
from .canonical import canonical_dumps


def _risk_level(f: Any) -> Any:
    return f.get("risk_level") if isinstance(f, dict) else getattr(f, "risk_level", None)


def _as_dict(f: Any) -> Dict[str, Any]:
    return f if isinstance(f, dict) else f.model_dump()


def dataset_fingerprint(ledger, purchase_orders, invoices, journal_entries) -> str:
    """SHA-256 over the canonical-JSON of ALL audited inputs — AS-1215 §.10
    proof of the exact (100%) population analysed."""
    blob = canonical_dumps({
        "ledger": ledger, "purchase_orders": purchase_orders,
        "invoices": invoices, "journal_entries": journal_entries,
    })
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def build_completion_document(tenant_id: str, findings: List[Any], fingerprint: str,
                              materiality: float) -> Dict[str, Any]:
    """Assemble the AS-1215 §.12 Engagement Completion Document."""
    risk_counts: Dict[str, int] = {}
    for f in findings:
        lvl = _risk_level(f)
        risk_counts[lvl] = risk_counts.get(lvl, 0) + 1
    return {
        "document_type": "EngagementCompletionDocument",
        "pcaob_standard": "AS 1215",
        "tenant_id": tenant_id,
        "dataset_fingerprint": fingerprint,
        "materiality_threshold": materiality,
        "findings": [_as_dict(f) for f in findings],
        "risk_counts": risk_counts,
        "n_findings": len(findings),
        "performed_by": {"agent": "FinancialAuditorAgent", "version": FINANCIAL_AUDITOR_VERSION},
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


_META_FIELDS = ("record_hash", "signature_b64", "signature_status", "signing_key_source", "verify_url")


def _signable(doc: Dict[str, Any]) -> Dict[str, Any]:
    """The document minus signature/hash metadata — the exact bytes signed and
    re-verified. Mirrors engine.strip_for_hashing's intent for this document."""
    return {k: v for k, v in doc.items() if k not in _META_FIELDS}


def sign_and_persist(doc: Dict[str, Any]) -> Dict[str, Any]:
    """Hash + ED25519-sign the document, persist it, append an immutable audit
    record. Refuses to sign if the active key is revoked (status 'unsigned')."""
    canonical = canonical_dumps(_signable(doc)).encode("utf-8")
    record_hash = hashlib.sha256(canonical).hexdigest()
    sig = None if cryptography.is_revoked() else signing.sign_bytes(canonical)
    status = "signed" if sig else "unsigned"
    stored = {**doc, "record_hash": record_hash, "signature_b64": sig,
              "signature_status": status, "signing_key_source": signing.signing_key_source()}
    persistence.write_artifact(record_hash, stored)
    audit_event("financial_audit_completed", {
        "record_hash": record_hash, "tenant_id": doc.get("tenant_id"),
        "dataset_fingerprint": doc.get("dataset_fingerprint"),
        "n_findings": doc.get("n_findings"), "signature_status": status,
    })
    return stored


def verify_report(record_hash: str) -> Dict[str, Any]:
    """Re-derive the signed bytes from the persisted artifact and verify."""
    art = persistence.read_artifact(record_hash)
    if not art:
        return {"verified": False, "reason": "not found", "record_hash": record_hash}
    sig = art.get("signature_b64")
    if not sig:
        return {"verified": False, "reason": "unsigned", "record_hash": record_hash}
    canonical = canonical_dumps(_signable(art)).encode("utf-8")
    ok = (hashlib.sha256(canonical).hexdigest() == record_hash
          and signing.verify_bytes(canonical, sig))
    return {"verified": bool(ok), "record_hash": record_hash,
            "signature_status": art.get("signature_status")}


def client_view(report: Dict[str, Any]) -> Dict[str, Any]:
    """Egress projection: a deep copy with PII redacted in the findings. The
    signed/persisted artifact is never mutated — redaction is display-only and
    must never feed back into the hash/signature."""
    from shared.pii_masking import redact_pii
    view = json.loads(json.dumps(report))
    view["findings"] = redact_pii(view.get("findings", []))
    return view
