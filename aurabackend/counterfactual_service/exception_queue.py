"""S34b — HITL exception queue over signed completion documents.

Findings flagged ``requires_human_review`` form a per-report queue. Each
human approve/override becomes (a) an immutable ``audit_human_override``
record in the WORM chain (AS 1215 contradiction documentation) and (b) its
own signed, content-addressed HumanOverrideRecord artifact, verifiable
through the same generic ``verify_report`` as the report itself.

Ground truth = signed decision artifacts + WORM log. The sidecar
``<report_hash>.decisions.json`` index (finding_id → decision record_hash)
is a convenience view for O(1) pending reads — losing it loses no evidence.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any, Dict

from shared.audit_log import audit_human_override
from shared.pii_masking import mask_pii_egress

from . import persistence
from .financial_report import _sign_document


class AlreadyDecidedError(RuntimeError):
    """A human decision for this finding already exists (decisions are
    final — the WORM stance; a wrong decision is corrected by a new run)."""


def _index_path(report_hash: str):
    # Hex-validate before building a path: report_hash arrives from a URL
    # segment, so this is the path-injection boundary.
    if not report_hash or not all(c in "0123456789abcdef" for c in report_hash):
        raise ValueError(f"record_hash must be lowercase hex; got {report_hash!r}")
    return persistence.artifact_dir() / f"{report_hash}.decisions.json"


def _read_index(report_hash: str) -> Dict[str, str]:
    path = _index_path(report_hash)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _write_index(report_hash: str, index: Dict[str, str]) -> None:
    path = _index_path(report_hash)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(index, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)  # atomic on POSIX & Windows


def _load_report(report_hash: str) -> Dict[str, Any]:
    art = persistence.read_artifact(report_hash)
    if not art or art.get("document_type") != "EngagementCompletionDocument":
        raise LookupError(f"no completion document for {report_hash}")
    return art


def pending_exceptions(report_hash: str) -> Dict[str, Any]:
    """Findings still awaiting a human decision, PII-redacted at egress
    (the signed artifact keeps raw evidence; redaction is display-only)."""
    report = _load_report(report_hash)
    decided = _read_index(report_hash)
    pending = [
        f for f in report.get("findings", [])
        if f.get("requires_human_review") and f.get("finding_id") not in decided
    ]
    return {
        "record_hash": report_hash,
        # Deep-copy before masking so the masking never leaks back
        # into the persisted (and signed) report object.
        "pending": mask_pii_egress(json.loads(json.dumps(pending)),
                                   context=str(report.get("tenant_id", ""))),
        "n_pending": len(pending),
        "n_decided": len(decided),
    }


def record_decision(report_hash: str, finding_id: str, human_auditor_id: str,
                    rationale: str, approved: bool) -> Dict[str, Any]:
    """Sign + persist a HumanOverrideRecord, append the WORM contradiction
    record, and mark the finding decided. Raises LookupError (unknown
    report/finding), AlreadyDecidedError, or ValueError (blank rationale)."""
    if not (rationale or "").strip():
        raise ValueError("AS 1215 requires a rationale for every human decision")
    report = _load_report(report_hash)
    reviewable = {
        f.get("finding_id") for f in report.get("findings", [])
        if f.get("requires_human_review")
    }
    if finding_id not in reviewable:
        raise LookupError(f"finding {finding_id} not reviewable in report {report_hash}")
    decided = _read_index(report_hash)
    if finding_id in decided:
        raise AlreadyDecidedError(
            f"finding {finding_id} already decided: {decided[finding_id]}")

    stored = _sign_document({
        "document_type": "HumanOverrideRecord",
        "pcaob_standard": "AS 1215",
        "report_record_hash": report_hash,
        "finding_id": finding_id,
        "human_auditor_id": human_auditor_id,
        "rationale": rationale,
        "approved": bool(approved),
        "decided_at": datetime.now(timezone.utc).isoformat(),
    })
    audit_human_override(finding_id, human_auditor_id, rationale, bool(approved))
    decided[finding_id] = stored["record_hash"]
    _write_index(report_hash, decided)
    return stored
