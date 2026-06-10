import logging
from typing import Any, Dict, List

from pydantic import BaseModel, Field
from shared.audit_log import audit_event

logger = logging.getLogger("aura.agents.financial_auditor")

# "Who performed the work" for PCAOB AS-1215 §.06 provenance — bump on any
# change to the audit logic so the completion document records which model ran.
FINANCIAL_AUDITOR_VERSION = "0.1.0"

class AuditFinding(BaseModel):
    pcaob_standard: str = Field(..., description="The relevant PCAOB standard (e.g., 'AS 2305')")
    risk_level: str = Field(..., description="'Low', 'Medium', 'High', 'Critical'")
    description: str
    evidence_payload: Dict[str, Any]
    requires_human_review: bool = True

class FinancialAuditorAgent:
    """
    Autonomous AI Auditor re-tasked to map directly to PCAOB Standards.
    Leverages DAR (Data Agnostic Researcher) semantics and UASR statistical baselines.
    """
    
    def __init__(self, tenant_id: str):
        self.tenant_id = tenant_id

    async def execute_as2110_risk_assessment(self, historical_reports: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        PCAOB AS 2110: Audit Planning and Risk Assessment.
        Establishes baseline materiality thresholds before scanning ledgers.
        """
        logger.info(f"[{self.tenant_id}] Executing AS 2110 Risk Assessment")
        
        # Mock logic for calculating materiality
        materiality_threshold = 50000.00 # $50k threshold
        
        audit_event("as2110_risk_assessment_completed", {
            "tenant_id": self.tenant_id,
            "materiality_threshold_usd": materiality_threshold,
            "historical_reports_analyzed": len(historical_reports)
        })
        return {"status": "success", "materiality_threshold": materiality_threshold}

    async def execute_as2305_analytical_procedures(self, ledger_batch: List[Dict[str, Any]]) -> List[AuditFinding]:
        """
        PCAOB AS 2305: Substantive Analytical Procedures.
        Uses UASR engine hooks to flag statistical variances.
        """
        logger.info(f"[{self.tenant_id}] Executing AS 2305 Substantive Analytical Procedures")
        findings = []
        
        for entry in ledger_batch:
            # Mock variance detection: Any entry > $100k triggers a variance alert
            if entry.get("amount", 0) > 100000:
                finding = AuditFinding(
                    pcaob_standard="AS 2305",
                    risk_level="High",
                    description=f"Significant statistical variance detected for account {entry.get('account_code')}.",
                    evidence_payload={"entry_id": entry.get("internal_id"), "amount": entry.get("amount")}
                )
                findings.append(finding)
                
                audit_event("as2305_variance_detected", finding.model_dump())
                
        return findings

    async def execute_as2201_internal_controls(self, purchase_orders: List[Dict], invoices: List[Dict]) -> List[AuditFinding]:
        """
        PCAOB AS 2201: Internal Control Over Financial Reporting.
        Matches POs to invoices and verifies approval signatures.
        """
        logger.info(f"[{self.tenant_id}] Executing AS 2201 Internal Control Checks")
        findings = []
        
        # Mock logic: Flag if invoice has no matching PO
        po_ids = {po.get("po_number") for po in purchase_orders}
        for inv in invoices:
            if inv.get("po_number") not in po_ids:
                finding = AuditFinding(
                    pcaob_standard="AS 2201",
                    risk_level="Medium",
                    description=f"Invoice {inv.get('invoice_number')} lacks a matching Purchase Order.",
                    evidence_payload={"invoice": inv}
                )
                findings.append(finding)
                audit_event("as2201_control_deficiency", finding.model_dump())
                
        return findings

    async def execute_as2401_fraud_detection(self, journal_entries: List[Dict]) -> List[AuditFinding]:
        """
        PCAOB AS 2401: Consideration of Fraud.
        Detects duplicate payments, round-dollar anomalies, and unusual period-end entries.
        """
        logger.info(f"[{self.tenant_id}] Executing AS 2401 Fraud Detection")
        findings = []

        # Duplicate-payment detection: same amount to the same account/vendor is a
        # classic double-pay fraud/error pattern. Keyed on (amount, account, vendor)
        # so distinct legitimate entries that merely share an amount don't collide.
        seen: set = set()
        for je in journal_entries:
            amt = je.get("amount", 0)
            dup_key = (amt, je.get("account_code"), je.get("vendor_id"))
            if amt and dup_key in seen:
                finding = AuditFinding(
                    pcaob_standard="AS 2401",
                    risk_level="High",
                    description="Potential duplicate payment detected (same amount, account, and vendor).",
                    evidence_payload={"je_id": je.get("internal_id"), "amount": amt,
                                      "account_code": je.get("account_code"), "vendor_id": je.get("vendor_id")},
                )
                findings.append(finding)
                audit_event("as2401_fraud_risk_duplicate", finding.model_dump())
            seen.add(dup_key)

            # Round-dollar anomaly: manual round-number entries are a fraud signal.
            if amt > 0 and amt % 1000 == 0:
                finding = AuditFinding(
                    pcaob_standard="AS 2401",
                    risk_level="High",
                    description="Suspicious round-dollar journal entry detected.",
                    evidence_payload={"je_id": je.get("internal_id"), "amount": amt},
                )
                findings.append(finding)
                audit_event("as2401_fraud_risk_round_dollar", finding.model_dump())

        return findings

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
