import logging
import math
from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from shared.audit_log import audit_event

logger = logging.getLogger("aura.agents.financial_auditor")

# "Who performed the work" for PCAOB AS-1215 §.06 provenance — bump on any
# change to the audit logic so the completion document records which model ran.
FINANCIAL_AUDITOR_VERSION = "0.4.0"

# AS-2110 materiality calibration (S38). Benchmark = 1% of total ledger
# activity (sum of |amount|, so credits count) — the conventional auditor
# rule-of-thumb for a volume benchmark. Performance materiality takes the
# standard 75% haircut so aggregation risk is covered. The floor stops a
# tiny sample batch from producing an absurdly low threshold, and the
# default applies when there is no financial basis to calibrate against.
MATERIALITY_BENCHMARK_PCT = 0.01
PERFORMANCE_HAIRCUT = 0.75
MATERIALITY_FLOOR = 10_000.0
DEFAULT_OVERALL_MATERIALITY = 50_000.0

# AS-2401 forensic constants (S39).
# Benford's Law first-digit expected frequencies P(d) = log10(1 + 1/d).
# A population of naturally-occurring amounts conforms; fabricated or
# manipulated numbers deviate. We score conformity with Nigrini's
# Mean Absolute Deviation (MAD) and treat MAD above the first-digit
# "nonconformity" cutoff as a fraud signal. Benford is only meaningful
# on a reasonably sized population, so it is gated on a minimum sample.
BENFORD_EXPECTED: Dict[int, float] = {d: math.log10(1 + 1 / d) for d in range(1, 10)}
BENFORD_MAD_NONCONFORMITY = 0.015  # Nigrini first-digit nonconformity cutoff
MIN_BENFORD_SAMPLE = 50
# Period-end cutoff window: entries posted within this many days of the
# period close are higher-risk for window dressing (revenue pulled
# forward, expenses deferred) and warrant cutoff testing.
CUTOFF_WINDOW_DAYS = 3


def _first_significant_digit(value: Any) -> Optional[int]:
    """Leading significant digit (1–9) of ``|value|``; None for zero,
    non-numeric, or non-finite inputs (which carry no Benford signal)."""
    try:
        v = abs(float(value))
    except (TypeError, ValueError):
        return None
    if not math.isfinite(v) or v == 0:
        return None
    while v < 1:
        v *= 10
    while v >= 10:
        v /= 10
    return int(v)


def _benford_first_digit_mad(amounts: List[Any]) -> tuple[Optional[float], int]:
    """Return ``(mad, n)`` — the first-digit Mean Absolute Deviation from
    Benford's Law over the numeric ``amounts``, and the sample size that
    contributed. ``mad`` is None when no amount yields a leading digit."""
    digits = [d for d in (_first_significant_digit(a) for a in amounts) if d is not None]
    n = len(digits)
    if n == 0:
        return None, 0
    counts = {d: 0 for d in range(1, 10)}
    for d in digits:
        counts[d] += 1
    mad = sum(abs(counts[d] / n - BENFORD_EXPECTED[d]) for d in range(1, 10)) / 9
    return mad, n


def _parse_date(value: Any) -> Optional[datetime]:
    """Best-effort ISO-8601 parse; None when absent or unparseable."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


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

    async def execute_as2110_risk_assessment(self, historical_reports: List[Dict[str, Any]],
                                             ledger: List[Dict[str, Any]] | None = None) -> Dict[str, Any]:
        """
        PCAOB AS 2110: Audit Planning and Risk Assessment.
        Calibrates materiality from the population under audit: overall =
        max(floor, 1% of total absolute ledger activity); the returned
        ``materiality_threshold`` is PERFORMANCE materiality (75% of overall),
        the operative scanning threshold for AS 2305.
        """
        logger.info(f"[{self.tenant_id}] Executing AS 2110 Risk Assessment")

        basis = float(sum(abs(entry.get("amount", 0) or 0) for entry in (ledger or [])))
        if basis > 0:
            overall = max(MATERIALITY_FLOOR, basis * MATERIALITY_BENCHMARK_PCT)
        else:
            overall = DEFAULT_OVERALL_MATERIALITY
        performance = overall * PERFORMANCE_HAIRCUT

        audit_event("as2110_risk_assessment_completed", {
            "tenant_id": self.tenant_id,
            "materiality_basis_usd": basis,
            "overall_materiality_usd": overall,
            "materiality_threshold_usd": performance,
            "historical_reports_analyzed": len(historical_reports)
        })
        return {"status": "success", "materiality_threshold": performance,
                "overall_materiality": overall, "materiality_basis": basis}

    async def execute_as2305_analytical_procedures(self, ledger_batch: List[Dict[str, Any]],
                                                   materiality_threshold: float,
                                                   historical_reports: Optional[List[Dict[str, Any]]] = None,
                                                   ) -> List[AuditFinding]:
        """
        PCAOB AS 2305: Substantive Analytical Procedures.
        Two substantive tests, each scaled to performance materiality:
        (1) absolute — any entry exceeding performance materiality; and
        (2) expectation — when prior-period history is supplied, an entry
        whose deviation from its account's historical mean exceeds
        performance materiality is flagged even if its absolute amount is
        modest. The threshold each entry was judged against travels in the
        evidence payload so the signed completion document is self-explanatory.
        """
        logger.info(f"[{self.tenant_id}] Executing AS 2305 Substantive Analytical Procedures")
        findings: List[AuditFinding] = []

        # Form a per-account expectation (mean prior amount) from history.
        expectations: Dict[Any, float] = {}
        if historical_reports:
            sums: Dict[Any, float] = {}
            counts: Dict[Any, int] = {}
            for rec in historical_reports:
                acct = rec.get("account_code")
                amt = rec.get("amount")
                if acct is None or amt is None:
                    continue
                sums[acct] = sums.get(acct, 0.0) + float(amt)
                counts[acct] = counts.get(acct, 0) + 1
            expectations = {a: sums[a] / counts[a] for a in sums}

        for entry in ledger_batch:
            amount = entry.get("amount", 0)
            acct = entry.get("account_code")
            if amount > materiality_threshold:
                finding = AuditFinding(
                    pcaob_standard="AS 2305",
                    risk_level="High",
                    description=f"Significant statistical variance detected for account {acct}.",
                    evidence_payload={"test": "absolute_materiality", "entry_id": entry.get("internal_id"),
                                      "amount": amount, "materiality_threshold": materiality_threshold}
                )
                findings.append(finding)
                audit_event("as2305_variance_detected", finding.model_dump())
            elif acct in expectations and abs(amount - expectations[acct]) > materiality_threshold:
                finding = AuditFinding(
                    pcaob_standard="AS 2305",
                    risk_level="High",
                    description=(f"Account {acct} deviates from its prior-period expectation "
                                 f"by more than performance materiality."),
                    evidence_payload={"test": "expectation_deviation", "entry_id": entry.get("internal_id"),
                                      "amount": amount, "expected": expectations[acct],
                                      "materiality_threshold": materiality_threshold}
                )
                findings.append(finding)
                audit_event("as2305_variance_detected", finding.model_dump())

        return findings

    async def execute_as2201_internal_controls(self, purchase_orders: List[Dict], invoices: List[Dict],
                                               goods_receipts: Optional[List[Dict]] = None,
                                               ) -> List[AuditFinding]:
        """
        PCAOB AS 2201: Internal Control Over Financial Reporting.
        Tests the procure-to-pay controls: a two-way PO↔invoice match
        (escalating to a three-way PO↔invoice↔goods-receipt match when
        goods receipts are supplied), and authorization controls —
        segregation of duties (an invoice's preparer must differ from its
        approver) and approval-authority limits (an invoice amount must
        not exceed the approver's authority). Authorization checks fire
        only when the relevant fields are present on the invoice.
        """
        logger.info(f"[{self.tenant_id}] Executing AS 2201 Internal Control Checks")
        findings: List[AuditFinding] = []

        po_ids = {po.get("po_number") for po in purchase_orders}
        gr_po_ids = {gr.get("po_number") for gr in goods_receipts} if goods_receipts is not None else None

        for inv in invoices:
            inv_po = inv.get("po_number")
            if inv_po not in po_ids:
                finding = AuditFinding(
                    pcaob_standard="AS 2201",
                    risk_level="Medium",
                    description=f"Invoice {inv.get('invoice_number')} lacks a matching Purchase Order.",
                    evidence_payload={"control": "two_way_match", "invoice": inv},
                )
                findings.append(finding)
                audit_event("as2201_control_deficiency", finding.model_dump())
            elif gr_po_ids is not None and inv_po not in gr_po_ids:
                finding = AuditFinding(
                    pcaob_standard="AS 2201",
                    risk_level="Medium",
                    description=(f"Invoice {inv.get('invoice_number')} has a Purchase Order but no goods "
                                 "receipt (three-way match failure)."),
                    evidence_payload={"control": "three_way_match", "invoice": inv},
                )
                findings.append(finding)
                audit_event("as2201_control_deficiency", finding.model_dump())

            approver = inv.get("approved_by")
            preparer = inv.get("entered_by")
            if approver is not None and preparer is not None and approver == preparer:
                finding = AuditFinding(
                    pcaob_standard="AS 2201",
                    risk_level="High",
                    description=(f"Invoice {inv.get('invoice_number')} was entered and approved by the same "
                                 "person (segregation-of-duties violation)."),
                    evidence_payload={"control": "segregation_of_duties", "person": approver,
                                      "invoice_number": inv.get("invoice_number")},
                )
                findings.append(finding)
                audit_event("as2201_control_deficiency", finding.model_dump())

            limit = inv.get("approval_limit")
            amount = inv.get("amount")
            if approver is not None and limit is not None and amount is not None and amount > limit:
                finding = AuditFinding(
                    pcaob_standard="AS 2201",
                    risk_level="High",
                    description=(f"Invoice {inv.get('invoice_number')} amount {amount} exceeds the approver's "
                                 f"authority limit {limit}."),
                    evidence_payload={"control": "approval_authority", "amount": amount,
                                      "approval_limit": limit, "approved_by": approver,
                                      "invoice_number": inv.get("invoice_number")},
                )
                findings.append(finding)
                audit_event("as2201_control_deficiency", finding.model_dump())

        return findings

    async def execute_as2401_fraud_detection(self, journal_entries: List[Dict],
                                             period_end: Optional[str] = None) -> List[AuditFinding]:
        """
        PCAOB AS 2401: Consideration of Fraud.
        Four substantive fraud tests: duplicate-payment detection, round-
        dollar anomalies, a Benford's-Law first-digit conformity test over
        the population of amounts, and — when a period-end date is supplied
        — cutoff testing that flags entries posted within the cutoff window
        of the period close (window-dressing risk).
        """
        logger.info(f"[{self.tenant_id}] Executing AS 2401 Fraud Detection")
        findings: List[AuditFinding] = []

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

        # Benford's-Law first-digit test over the whole population (gated on
        # sample size so the statistic is meaningful).
        mad, n_benford = _benford_first_digit_mad([je.get("amount") for je in journal_entries])
        if mad is not None and n_benford >= MIN_BENFORD_SAMPLE and mad > BENFORD_MAD_NONCONFORMITY:
            finding = AuditFinding(
                pcaob_standard="AS 2401",
                risk_level="High",
                description=(f"First-digit distribution of {n_benford} entries deviates from Benford's Law "
                             f"(MAD={mad:.4f} > {BENFORD_MAD_NONCONFORMITY}); amounts may be fabricated."),
                evidence_payload={"test": "benford_first_digit", "n": n_benford,
                                  "mad": round(mad, 6), "threshold": BENFORD_MAD_NONCONFORMITY},
            )
            findings.append(finding)
            audit_event("as2401_fraud_risk_benford", finding.model_dump())

        # Period-end cutoff: entries posted within the cutoff window of the
        # period close (window-dressing risk). Gated on dates being present.
        period_end_dt = _parse_date(period_end)
        if period_end_dt is not None:
            for je in journal_entries:
                posting_dt = _parse_date(je.get("posting_date"))
                if posting_dt is None:
                    continue
                days_before = (period_end_dt.date() - posting_dt.date()).days
                if 0 <= days_before <= CUTOFF_WINDOW_DAYS:
                    finding = AuditFinding(
                        pcaob_standard="AS 2401",
                        risk_level="Medium",
                        description=(f"Journal entry posted within {CUTOFF_WINDOW_DAYS} days of period end "
                                     "(cutoff / window-dressing risk)."),
                        evidence_payload={"test": "period_end_cutoff", "je_id": je.get("internal_id"),
                                          "posting_date": je.get("posting_date"), "period_end": period_end,
                                          "days_before_period_end": days_before},
                    )
                    findings.append(finding)
                    audit_event("as2401_fraud_risk_cutoff", finding.model_dump())

        return findings

    async def run_full_audit(self, ledger, purchase_orders, invoices, journal_entries,
                             historical_reports=None, goods_receipts=None, period_end=None):
        """Run AS-2110/2305/2201/2401 over RAW inputs (fraud cross-checks need the
        real employee/vendor fields). Returns {findings, materiality_threshold}."""
        risk = await self.execute_as2110_risk_assessment(historical_reports or [], ledger=ledger)
        findings = []
        findings += await self.execute_as2305_analytical_procedures(
            ledger, risk["materiality_threshold"], historical_reports)
        findings += await self.execute_as2201_internal_controls(
            purchase_orders, invoices, goods_receipts)
        findings += await self.execute_as2401_fraud_detection(journal_entries, period_end)
        return {"findings": findings, "materiality_threshold": risk["materiality_threshold"]}
