"""Subsystem C — Task 2: provable inputs + audit-grade identity on the
signed completion document.

The fingerprint must bind ALL audited inputs (today only 4 of 7), and the
completion document must carry the subject (so multiple audits link) and the
preparer assignment (AS 1215), inside the signed bytes."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from counterfactual_service import financial_report as fr


def test_fingerprint_binds_all_seven_inputs():
    base = ([{"amount": 1}], [{"po": 1}], [{"inv": 1}], [{"je": 1}])
    f0 = fr.dataset_fingerprint(*base)
    # changing any of the three previously-unbound inputs must change the proof
    f_gr = fr.dataset_fingerprint(*base, goods_receipts=[{"gr": 9}])
    f_hr = fr.dataset_fingerprint(*base, historical_reports=[{"hr": 9}])
    f_pe = fr.dataset_fingerprint(*base, period_end="2026-03-31")
    assert f0 != f_gr != f0
    assert f0 != f_hr
    assert f0 != f_pe
    # determinism preserved
    assert fr.dataset_fingerprint(*base, goods_receipts=[{"gr": 9}]) == f_gr


def test_completion_document_carries_subject_and_assignment():
    doc = fr.build_completion_document(
        "tenant-1", [{"pcaob_standard": "AS 2305", "risk_level": "High"}],
        "f" * 64, 50000.0,
        subject_id="model-underwriting-v3", subject_type="model", preparer_id="ada@bank.test",
    )
    assert doc["subject_id"] == "model-underwriting-v3"
    assert doc["subject_type"] == "model"
    assert doc["assignment"]["preparer_id"] == "ada@bank.test"


def test_subject_and_assignment_are_inside_the_signed_bytes():
    # the signable projection (what gets ED25519-signed) must include the
    # subject + assignment, so they can't be swapped after signing
    doc = fr.build_completion_document(
        "t1", [], "f" * 64, 1000.0,
        subject_id="cohort-Q1", subject_type="decision_cohort", preparer_id="rob@bank.test",
    )
    signable = fr._signable(doc)
    assert signable["subject_id"] == "cohort-Q1"
    assert signable["assignment"]["preparer_id"] == "rob@bank.test"


def test_defaults_keep_existing_callers_working():
    # 4-arg callers (existing tests / legacy path) still build a valid doc
    doc = fr.build_completion_document("t1", [], "f" * 64, 1000.0)
    assert doc["subject_type"] == "dataset"
    assert "assignment" in doc
