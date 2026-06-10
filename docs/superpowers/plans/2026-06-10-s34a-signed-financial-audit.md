# S34a Signed Financial Audit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `POST /audit/financial` runs the PCAOB checks on a ledger, signs an AS-1215 Engagement Completion Document with AURA's persistent ED25519 key, writes it to the hash-chained audit log, and returns a signed, independently-verifiable report.

**Architecture:** Reuse existing primitives — `signing.py` (persistent ED25519 `sign_bytes`/`verify_bytes`), `persistence.py` (`write_artifact`/`read_artifact`), `canonical.py` (`canonical_dumps`), `audit_log.py` (hash-chained `audit_event`). New pure logic in `financial_report.py`; `FinancialAuditorAgent` gains a `run_full_audit` composer; `cryptography.py` is reconciled to expose `signing.py`'s key via `/jwks` (in-memory keystore removed). PII raw in-boundary; redacted only on the egress `client_view`.

**Tech Stack:** Python 3.11/3.12, FastAPI, pydantic v2, `cryptography` (ed25519), hashlib. Tests: pytest. No new deps.

**Spec:** `docs/superpowers/specs/2026-06-10-s34a-signed-financial-audit-design.md`

**Local test runner:** repo-root `.venv/Scripts/python.exe` (e.g. `cd aurabackend && "../.venv/Scripts/python.exe" -m pytest tests/test_financial_audit.py -q`).

---

## File Structure

- **Modify** `aurabackend/counterfactual_service/signing.py` — add `public_key_raw_b64url()`.
- **Modify** `aurabackend/counterfactual_service/cryptography.py` — reconcile onto `signing.py`: single-key `get_jwks()`, persisted revocation (`is_revoked`/`soft_revoke_key`), remove the in-memory keystore.
- **Modify** `aurabackend/tests/test_finance_pivot.py` — update the 3 crypto tests to the reconciled single-key model.
- **Create** `aurabackend/counterfactual_service/financial_report.py` — `dataset_fingerprint`, `build_completion_document`, `sign_and_persist`, `verify_report`, `client_view`.
- **Modify** `aurabackend/agents/specialists/financial_auditor.py` — add `FINANCIAL_AUDITOR_VERSION` + `run_full_audit`.
- **Modify** `aurabackend/counterfactual_service/main.py` — `FinancialAuditRequest` + `POST /audit/financial` + `GET /audit/financial/verify/{hash}`.
- **Modify** `aurabackend/api_gateway/routers/counterfactual.py` — proxy the two routes.
- **Create** `aurabackend/tests/test_financial_audit.py` — report, agent, client_view, endpoint e2e (Tier A).

---

### Task 1: Reconcile `cryptography.py` onto `signing.py`

**Files:**
- Modify: `aurabackend/counterfactual_service/signing.py`
- Modify: `aurabackend/counterfactual_service/cryptography.py`
- Test: `aurabackend/tests/test_finance_pivot.py`

- [ ] **Step 1: Add the raw-public-key helper to `signing.py`** (after `public_key_pem`)

```python
def public_key_raw_b64url() -> Optional[str]:
    """Return the persistent public key as base64url (no padding) of its raw
    32 bytes — the form a JWK `x` field requires. None if signing unavailable."""
    pair = _resolve_key_pair()
    if pair is None:
        return None
    _, public_key = pair
    try:
        raw = public_key.public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
    except Exception as exc:  # pragma: no cover
        logger.warning("raw public key export failed: %s", exc)
        return None
```

- [ ] **Step 2: Replace `cryptography.py` body** with the reconciled module

```python
"""ED25519 JWKS + soft revocation over AURA's persistent signing key.

This module no longer holds its own keys: it reflects the ONE persistent key
managed by ``signing.py`` (so historical signatures stay verifiable across
restarts), and persists a small revocation set. Findings/documents are signed
directly via ``signing.sign_bytes``.
"""
import json
import logging
import os
from pathlib import Path
from typing import Dict, Set

from . import signing

logger = logging.getLogger("aura.cryptography")

_KID = "aura-ed25519"


def _revoked_path() -> Path:
    key_dir = os.getenv("AURA_SIGNING_KEY_DIR", "data/keys").strip() or "data/keys"
    return Path(key_dir) / "revoked_kids.json"


def _load_revoked() -> Set[str]:
    try:
        return set(json.loads(_revoked_path().read_text(encoding="utf-8")))
    except Exception:
        return set()


def is_revoked(kid: str = _KID) -> bool:
    return kid in _load_revoked()


def soft_revoke_key(kid: str = _KID) -> None:
    """Soft revocation: flag the kid for FUTURE signing (historical signatures
    stay valid via JWKS). Persisted so it survives restarts."""
    revoked = _load_revoked()
    revoked.add(kid)
    path = _revoked_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(sorted(revoked)), encoding="utf-8")
    logger.warning("Key %s soft-revoked; historical signatures remain valid.", kid)


def get_jwks() -> Dict:
    """JWKS for the single persistent signing key. Empty if signing unavailable."""
    x = signing.public_key_raw_b64url()
    if x is None:
        return {"keys": []}
    return {"keys": [{
        "kty": "OKP", "crv": "Ed25519", "kid": _KID, "x": x,
        "revoked": is_revoked(_KID),
    }]}
```

- [ ] **Step 3: Update the crypto tests** in `tests/test_finance_pivot.py` — replace the three tests (`test_revoked_key_cannot_sign`, `test_jwks_marks_revoked_key`, `test_signature_verifies_against_published_key`) and the unused imports with:

```python
def test_jwks_reflects_persistent_signing_key():
    jwks = crypto.get_jwks()
    assert len(jwks["keys"]) == 1
    k = jwks["keys"][0]
    assert k["kty"] == "OKP" and k["crv"] == "Ed25519"
    assert k["x"] == signing.public_key_raw_b64url()


def test_soft_revoke_persists_and_marks_jwks(tmp_path, monkeypatch):
    monkeypatch.setenv("AURA_SIGNING_KEY_DIR", str(tmp_path))
    crypto.soft_revoke_key("aura-ed25519")
    assert crypto.is_revoked("aura-ed25519") is True
    assert crypto.get_jwks()["keys"][0]["revoked"] is True


def test_signing_roundtrips_via_signing_module():
    import base64
    sig = signing.sign_bytes(b"hello")
    assert sig and signing.verify_bytes(b"hello", sig)
    assert not signing.verify_bytes(b"tampered", sig)
```

  Update the imports at the top of `test_finance_pivot.py`: replace
  `from counterfactual_service import cryptography as crypto` line's neighbours so the file imports
  `from counterfactual_service import cryptography as crypto, signing` and drops the now-unused
  `serialization`/`FinancialAuditorAgent` imports only if no other test uses them (the auditor
  test below still uses `FinancialAuditorAgent`, so keep it).

- [ ] **Step 4: Run the tests**

Run: `cd aurabackend && "../.venv/Scripts/python.exe" -m pytest tests/test_finance_pivot.py -q`
Expected: PASS (the 3 rewritten crypto tests + the untouched PII/auditor/admin tests)

- [ ] **Step 5: Commit**

```bash
git add aurabackend/counterfactual_service/signing.py aurabackend/counterfactual_service/cryptography.py aurabackend/tests/test_finance_pivot.py
git commit -m "refactor(s34a): reconcile cryptography.py onto persistent signing.py key"
```

---

### Task 2: `dataset_fingerprint` + `build_completion_document`

**Files:**
- Create: `aurabackend/counterfactual_service/financial_report.py`
- Test: `aurabackend/tests/test_financial_audit.py`

- [ ] **Step 1: Write the failing test**

```python
# aurabackend/tests/test_financial_audit.py
"""S34a — signed financial audit: report assembly, signing, verify, egress."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from counterfactual_service import financial_report as fr


def test_dataset_fingerprint_deterministic_and_sensitive():
    a = fr.dataset_fingerprint([{"amount": 1}], [], [], [])
    b = fr.dataset_fingerprint([{"amount": 1}], [], [], [])
    c = fr.dataset_fingerprint([{"amount": 2}], [], [], [])
    assert a == b and a != c
    assert len(a) == 64  # sha256 hex


def test_build_completion_document_shape():
    findings = [{"pcaob_standard": "AS 2305", "risk_level": "High", "description": "x",
                 "evidence_payload": {}, "requires_human_review": True}]
    doc = fr.build_completion_document("tenant-1", findings, "fp" * 16, 50000.0)
    assert doc["document_type"] == "EngagementCompletionDocument"
    assert doc["pcaob_standard"] == "AS 1215"
    assert doc["tenant_id"] == "tenant-1"
    assert doc["dataset_fingerprint"] == "fp" * 16
    assert doc["materiality_threshold"] == 50000.0
    assert doc["n_findings"] == 1
    assert doc["risk_counts"] == {"High": 1}
    assert doc["performed_by"]["agent"] == "FinancialAuditorAgent"
    assert "generated_at" in doc
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd aurabackend && "../.venv/Scripts/python.exe" -m pytest tests/test_financial_audit.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'counterfactual_service.financial_report'`

- [ ] **Step 3: Write minimal implementation**

```python
# aurabackend/counterfactual_service/financial_report.py
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
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd aurabackend && "../.venv/Scripts/python.exe" -m pytest tests/test_financial_audit.py -q`
Expected: FAIL — `ImportError: cannot import name 'FINANCIAL_AUDITOR_VERSION'` (defined in Task 4). To unblock now, the import will resolve once Task 4 lands; if running this task in isolation, temporarily define `FINANCIAL_AUDITOR_VERSION = "0.1.0"` at the top of `financial_report.py` and move it to the agent in Task 4. PREFERRED: implement Task 4 Step 3 (the constant) first, then this passes. After both: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add aurabackend/counterfactual_service/financial_report.py aurabackend/tests/test_financial_audit.py
git commit -m "feat(s34a): dataset fingerprint + AS-1215 completion document"
```

---

### Task 3: `sign_and_persist` + `verify_report` (with revoke guard)

**Files:**
- Modify: `aurabackend/counterfactual_service/financial_report.py`
- Test: `aurabackend/tests/test_financial_audit.py`

- [ ] **Step 1: Write the failing test** (append)

```python
def _store(monkeypatch):
    store = {}
    monkeypatch.setattr(fr.persistence, "write_artifact", lambda h, p: store.__setitem__(h, p) or h)
    monkeypatch.setattr(fr.persistence, "read_artifact", lambda h: store.get(h))
    monkeypatch.setattr(fr, "audit_event", lambda *a, **k: None)
    return store


def _doc():
    return fr.build_completion_document("t1", [{"pcaob_standard": "AS 2305", "risk_level": "High",
        "description": "x", "evidence_payload": {"amount": 1}, "requires_human_review": True}],
        "f" * 64, 50000.0)


def test_sign_and_verify_roundtrip(monkeypatch):
    _store(monkeypatch)
    stored = fr.sign_and_persist(_doc())
    assert stored["signature_status"] == "signed"
    assert len(stored["record_hash"]) == 64
    v = fr.verify_report(stored["record_hash"])
    assert v["verified"] is True


def test_verify_detects_tampering(monkeypatch):
    store = _store(monkeypatch)
    stored = fr.sign_and_persist(_doc())
    store[stored["record_hash"]]["findings"][0]["evidence_payload"]["amount"] = 999  # tamper
    assert fr.verify_report(stored["record_hash"])["verified"] is False


def test_verify_missing_returns_false(monkeypatch):
    _store(monkeypatch)
    assert fr.verify_report("0" * 64)["verified"] is False


def test_revoked_key_yields_unsigned(monkeypatch):
    _store(monkeypatch)
    monkeypatch.setattr(fr.cryptography, "is_revoked", lambda kid=None: True)
    stored = fr.sign_and_persist(_doc())
    assert stored["signature_status"] == "unsigned"
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd aurabackend && "../.venv/Scripts/python.exe" -m pytest tests/test_financial_audit.py -k "sign_and_verify or tampering or missing or revoked" -q`
Expected: FAIL — `AttributeError: module 'counterfactual_service.financial_report' has no attribute 'sign_and_persist'`

- [ ] **Step 3: Implement** (append to `financial_report.py`)

```python
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
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd aurabackend && "../.venv/Scripts/python.exe" -m pytest tests/test_financial_audit.py -k "sign_and_verify or tampering or missing or revoked" -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add aurabackend/counterfactual_service/financial_report.py aurabackend/tests/test_financial_audit.py
git commit -m "feat(s34a): sign+persist+verify the completion document"
```

---

### Task 4: `run_full_audit` on `FinancialAuditorAgent`

**Files:**
- Modify: `aurabackend/agents/specialists/financial_auditor.py`
- Test: `aurabackend/tests/test_financial_audit.py`

- [ ] **Step 1: Write the failing test** (append)

```python
import asyncio


def test_run_full_audit_spans_standards(monkeypatch):
    import agents.specialists.financial_auditor as fa
    monkeypatch.setattr(fa, "audit_event", lambda *a, **k: None)
    agent = fa.FinancialAuditorAgent(tenant_id="t1")
    ledger = [{"internal_id": "L1", "account_code": "4000", "amount": 250000.0}]   # AS 2305 variance
    pos = [{"po_number": "PO-1"}]
    invoices = [{"invoice_number": "INV-9", "po_number": "PO-MISSING",
                 "employee_name": "Ada Lovelace"}]                                  # AS 2201 unmatched
    jes = [{"internal_id": "J1", "amount": 5000.0, "account_code": "6000", "vendor_id": "V1"},
           {"internal_id": "J2", "amount": 5000.0, "account_code": "6000", "vendor_id": "V1"}]  # AS 2401 dup+round
    result = asyncio.run(agent.run_full_audit(ledger, pos, invoices, jes))
    stds = {f.pcaob_standard for f in result["findings"]}
    assert {"AS 2305", "AS 2201", "AS 2401"}.issubset(stds)
    assert result["materiality_threshold"] == 50000.0


def test_run_full_audit_clean_batch_no_findings(monkeypatch):
    import agents.specialists.financial_auditor as fa
    monkeypatch.setattr(fa, "audit_event", lambda *a, **k: None)
    agent = fa.FinancialAuditorAgent(tenant_id="t1")
    result = asyncio.run(agent.run_full_audit(
        [{"internal_id": "L", "account_code": "4000", "amount": 100.0}],
        [{"po_number": "PO-1"}], [{"invoice_number": "I", "po_number": "PO-1"}],
        [{"internal_id": "J", "amount": 123.45, "account_code": "6000", "vendor_id": "V"}]))
    assert result["findings"] == []
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd aurabackend && "../.venv/Scripts/python.exe" -m pytest tests/test_financial_audit.py -k run_full_audit -q`
Expected: FAIL — `AttributeError: 'FinancialAuditorAgent' object has no attribute 'run_full_audit'`

- [ ] **Step 3: Implement** — add the version constant near the top of `financial_auditor.py` (after `logger = ...`) and the method to the class

```python
FINANCIAL_AUDITOR_VERSION = "0.1.0"
```

```python
    async def run_full_audit(self, ledger, purchase_orders, invoices, journal_entries,
                             historical_reports=None):
        """Run AS-2110/2305/2201/2401 over RAW inputs (fraud cross-checks need the
        real employee/vendor fields). Returns {findings, materiality_threshold}."""
        risk = await self.execute_as2110_risk_assessment(historical_reports or [])
        findings = []
        findings += await self.execute_as2305_analytical_procedures(ledger)
        findings += await self.execute_as2201_internal_controls(purchase_orders, invoices)
        findings += await self.execute_as2401_fraud_detection(journal_entries)
        return {"findings": findings, "materiality_threshold": risk["materiality_threshold"]}
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd aurabackend && "../.venv/Scripts/python.exe" -m pytest tests/test_financial_audit.py -k run_full_audit -v`
Expected: PASS (2 tests). Also re-run the whole file: `pytest tests/test_financial_audit.py -q` → all green (Task 2's `FINANCIAL_AUDITOR_VERSION` import now resolves).

- [ ] **Step 5: Commit**

```bash
git add aurabackend/agents/specialists/financial_auditor.py aurabackend/tests/test_financial_audit.py
git commit -m "feat(s34a): run_full_audit composes the 4 PCAOB checks"
```

---

### Task 5: `client_view` egress PII redaction

**Files:**
- Modify: `aurabackend/counterfactual_service/financial_report.py`
- Test: `aurabackend/tests/test_financial_audit.py`

- [ ] **Step 1: Write the failing test** (append)

```python
def test_client_view_redacts_evidence_but_artifact_keeps_raw(monkeypatch):
    _store(monkeypatch)
    doc = fr.build_completion_document("t1", [{"pcaob_standard": "AS 2201", "risk_level": "Medium",
        "description": "unmatched", "evidence_payload": {"invoice": {"employee_name": "Ada", "po_number": "x"}},
        "requires_human_review": True}], "f" * 64, 50000.0)
    stored = fr.sign_and_persist(doc)
    view = fr.client_view(stored)
    # Egress view is redacted ...
    assert view["findings"][0]["evidence_payload"]["invoice"]["employee_name"] == "[REDACTED]"
    # ... but the signed artifact retains raw evidence AND still verifies.
    assert stored["findings"][0]["evidence_payload"]["invoice"]["employee_name"] == "Ada"
    assert fr.verify_report(stored["record_hash"])["verified"] is True
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd aurabackend && "../.venv/Scripts/python.exe" -m pytest tests/test_financial_audit.py -k client_view -q`
Expected: FAIL — `AttributeError: module 'counterfactual_service.financial_report' has no attribute 'client_view'`

- [ ] **Step 3: Implement** (append to `financial_report.py`)

```python
def client_view(report: Dict[str, Any]) -> Dict[str, Any]:
    """Egress projection: a deep copy with PII redacted in the findings. The
    signed/persisted artifact is never mutated — redaction is display-only and
    must never feed back into the hash/signature."""
    from shared.pii_masking import redact_pii
    view = json.loads(json.dumps(report))
    view["findings"] = redact_pii(view.get("findings", []))
    return view
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd aurabackend && "../.venv/Scripts/python.exe" -m pytest tests/test_financial_audit.py -k client_view -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add aurabackend/counterfactual_service/financial_report.py aurabackend/tests/test_financial_audit.py
git commit -m "feat(s34a): PII-redacted egress client_view (artifact stays raw)"
```

---

### Task 6: Endpoints + gateway proxy + e2e

**Files:**
- Modify: `aurabackend/counterfactual_service/main.py` (add near the other admin/jwks routes, ~line 480)
- Modify: `aurabackend/api_gateway/routers/counterfactual.py`
- Test: `aurabackend/tests/test_financial_audit.py`

- [ ] **Step 1: Write the failing test** (append) — exercises the handlers directly (no TestClient)

```python
def test_financial_audit_endpoint_e2e(monkeypatch):
    _store(monkeypatch)
    import counterfactual_service.main as m
    monkeypatch.setattr(m, "audit_event", lambda *a, **k: None) if hasattr(m, "audit_event") else None
    req = m.FinancialAuditRequest(
        tenant_id="t1",
        ledger=[{"internal_id": "L1", "account_code": "4000", "amount": 250000.0}],
        purchase_orders=[{"po_number": "PO-1"}],
        invoices=[{"invoice_number": "INV-9", "po_number": "PO-MISSING", "employee_name": "Ada"}],
        journal_entries=[{"internal_id": "J1", "amount": 5000.0, "account_code": "6000", "vendor_id": "V1"}],
    )
    report = asyncio.run(m.financial_audit(req))
    assert report["signature_status"] == "signed"
    assert report["verify_url"].endswith(report["record_hash"])
    # egress redaction applied in the response
    ev = report["findings"][0]["evidence_payload"]
    assert "Ada" not in str(ev)
    # independently verifiable
    v = asyncio.run(m.financial_audit_verify(report["record_hash"]))
    assert v["verified"] is True
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd aurabackend && "../.venv/Scripts/python.exe" -m pytest tests/test_financial_audit.py -k endpoint_e2e -q`
Expected: FAIL — `AttributeError: module 'counterfactual_service.main' has no attribute 'FinancialAuditRequest'`

- [ ] **Step 3: Add the request model + endpoints to `main.py`** (insert after the `/jwks`/`revoke-key` block, before the Sprint-13 section)

```python
class FinancialAuditRequest(BaseModel):
    tenant_id: str
    ledger: List[Dict[str, Any]] = Field(default_factory=list)
    purchase_orders: List[Dict[str, Any]] = Field(default_factory=list)
    invoices: List[Dict[str, Any]] = Field(default_factory=list)
    journal_entries: List[Dict[str, Any]] = Field(default_factory=list)
    historical_reports: List[Dict[str, Any]] = Field(default_factory=list)


@app.post("/audit/financial")
async def financial_audit(req: FinancialAuditRequest) -> Dict[str, Any]:
    from agents.specialists.financial_auditor import FinancialAuditorAgent

    from .financial_report import (
        build_completion_document,
        client_view,
        dataset_fingerprint,
        sign_and_persist,
    )
    agent = FinancialAuditorAgent(tenant_id=req.tenant_id)
    result = await agent.run_full_audit(
        req.ledger, req.purchase_orders, req.invoices, req.journal_entries, req.historical_reports)
    fingerprint = dataset_fingerprint(req.ledger, req.purchase_orders, req.invoices, req.journal_entries)
    doc = build_completion_document(req.tenant_id, result["findings"], fingerprint,
                                    result["materiality_threshold"])
    stored = sign_and_persist(doc)
    view = client_view(stored)
    view["verify_url"] = f"/audit/financial/verify/{stored['record_hash']}"
    return view


@app.get("/audit/financial/verify/{record_hash}")
async def financial_audit_verify(record_hash: str) -> Dict[str, Any]:
    from .financial_report import verify_report
    return verify_report(record_hash)
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd aurabackend && "../.venv/Scripts/python.exe" -m pytest tests/test_financial_audit.py -q`
Expected: PASS (all financial-audit tests)

- [ ] **Step 5: Add the gateway proxy** to `api_gateway/routers/counterfactual.py` — mirror the existing `/audit` proxy. After the existing financial-unrelated proxies, add:

```python
from counterfactual_service.main import (
    FinancialAuditRequest,
    financial_audit as _svc_financial_audit,
    financial_audit_verify as _svc_financial_audit_verify,
)


@router.post("/audit/financial")
async def proxy_financial_audit(req: FinancialAuditRequest) -> Dict[str, Any]:
    return await _svc_financial_audit(req)


@router.get("/audit/financial/verify/{record_hash}")
async def proxy_financial_audit_verify(record_hash: str) -> Dict[str, Any]:
    return await _svc_financial_audit_verify(record_hash)
```

  (Confirm `Dict`/`Any` are imported in that router; if not, add `from typing import Any, Dict`.)

- [ ] **Step 6: Run the full financial suite + the existing pivot suite + a regression sample**

Run: `cd aurabackend && "../.venv/Scripts/python.exe" -m pytest tests/test_financial_audit.py tests/test_finance_pivot.py tests/test_verdict.py -q`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add aurabackend/counterfactual_service/main.py aurabackend/api_gateway/routers/counterfactual.py aurabackend/tests/test_financial_audit.py
git commit -m "feat(s34a): POST /audit/financial + verify endpoint + gateway proxy"
```

---

## Self-Review

**1. Spec coverage:**
- Reconcile crypto onto signing.py + single-key JWKS + persisted revocation → Task 1. ✓
- `run_full_audit` over raw data → Task 4. ✓
- `dataset_fingerprint` (100% population) + AS-1215 completion document → Task 2. ✓
- Sign with persistent key + persist + immutable audit-log record → Task 3. ✓
- `verify_report` (recompute + verify_bytes; tamper→False) → Task 3. ✓
- Revoked key → unsigned (fail-closed) → Task 3. ✓
- PII raw in-boundary, redacted at egress (`client_view`); artifact retains raw + verifies → Task 5. ✓
- `POST /audit/financial` + `GET …/verify/{hash}` + gateway proxy → Task 6. ✓
- Error: no signing key → unsigned, never 500 → Task 3 (`sig` None → status unsigned). ✓
- `FinancialAuditorAgent` stays a service class → no BaseAgent task. ✓ (non-goal)

**2. Placeholder scan:** No TBD/TODO. Every code step is complete. The Task 2/4 ordering note (FINANCIAL_AUDITOR_VERSION) is explicit, not a placeholder — implement Task 4 Step 3's constant first if running Task 2 standalone.

**3. Type consistency:** `run_full_audit` returns `{"findings", "materiality_threshold"}` (Task 4) consumed identically in Task 6. `build_completion_document(tenant_id, findings, fingerprint, materiality)` signature matches its Task 6 call. `sign_and_persist`/`verify_report`/`client_view`/`dataset_fingerprint` names consistent across Tasks 2/3/5/6. `cryptography.is_revoked()` (Task 1) used in Task 3. `FINANCIAL_AUDITOR_VERSION` defined Task 4, imported Task 2. `_signable`/`_META_FIELDS` internal to financial_report. ✓
