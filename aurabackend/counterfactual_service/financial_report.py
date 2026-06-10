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
