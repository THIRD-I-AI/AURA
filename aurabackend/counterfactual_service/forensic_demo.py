"""S40 — canned dataset for the one-click forensic audit demo.

Engineered so a single ``/audit/financial/demo`` run trips every PCAOB
technique the auditor implements, giving the investor walkthrough a concrete
finding for each standard. Kept out of ``main.py`` so tests can import the
dataset without loading the FastAPI app.
"""
from typing import Any, Dict


def forensic_demo_dataset() -> Dict[str, Any]:
    """A deliberately fraud-laden books-and-records dataset."""
    # 55 fabricated expense entries clustered in the $1,000s/$2,000s — first
    # digits 1 and 2 dominate, a gross Benford violation. Distinct amounts +
    # vendors and non-round cents so the duplicate and round-dollar rules
    # don't fire on the population; mid-period dates so cutoff doesn't either.
    fabricated = [
        {
            "internal_id": f"JE-F{i:03d}",
            "amount": (1000 if i % 2 == 0 else 2000) + i + 0.50,
            "account_code": "6200",
            "vendor_id": f"VEND-{i:03d}",
            "posting_date": "2025-06-15",
        }
        for i in range(55)
    ]
    journal_entries = fabricated + [
        # Duplicate payment: same amount/account/vendor on consecutive days.
        {"internal_id": "JE-DUP1", "amount": 48000.0, "account_code": "6000",
         "vendor_id": "ACME", "posting_date": "2025-03-10"},
        {"internal_id": "JE-DUP2", "amount": 48000.0, "account_code": "6000",
         "vendor_id": "ACME", "posting_date": "2025-03-11"},
        # Round-dollar manual entry.
        {"internal_id": "JE-RND1", "amount": 25000.0, "account_code": "6100",
         "vendor_id": "GLOBEX", "posting_date": "2025-05-01"},
        # Posted one day before the period close — cutoff / window-dressing.
        {"internal_id": "JE-CUT1", "amount": 19875.40, "account_code": "4000",
         "vendor_id": "INITECH", "posting_date": "2025-12-30"},
    ]
    return {
        "tenant_id": "demo-tenant",
        "journal_entries": journal_entries,
        "ledger": [
            # Above performance materiality -> AS-2305 absolute variance.
            {"internal_id": "L-1001", "account_code": "4000", "amount": 250000.0},
            # Modest amount, but ~98% below the account's historical mean ->
            # AS-2305 expectation deviation.
            {"internal_id": "L-1002", "account_code": "5000", "amount": 1200.0},
        ],
        "historical_reports": [
            {"account_code": "5000", "amount": 80000.0},
            {"account_code": "5000", "amount": 82000.0},
        ],
        "purchase_orders": [{"po_number": "PO-7001"}, {"po_number": "PO-7002"}],
        # PO-7002 was invoiced but never received -> AS-2201 three-way failure.
        "goods_receipts": [{"po_number": "PO-7001"}],
        "invoices": [
            # No matching PO at all -> two-way match failure.
            {"invoice_number": "INV-9001", "po_number": "PO-MISSING"},
            # Has a PO but no goods receipt -> three-way match failure.
            {"invoice_number": "INV-9002", "po_number": "PO-7002"},
            # Entered and approved by the same person -> segregation of duties.
            {"invoice_number": "INV-9003", "po_number": "PO-7001",
             "entered_by": "j.doe", "approved_by": "j.doe"},
            # Amount exceeds the approver's authority limit.
            {"invoice_number": "INV-9004", "po_number": "PO-7001",
             "approved_by": "m.smith", "amount": 95000.0, "approval_limit": 25000.0},
        ],
        "period_end": "2025-12-31",
    }
