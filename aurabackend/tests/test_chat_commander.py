"""Unit guards for the chat 'commander' helpers (audit intent): monetary-column
detection and the population-level forensic scan. Pure functions — no LLM, no
network. Live end-to-end behaviour is exercised separately."""
from __future__ import annotations

from api_gateway.routers.chat import (
    _forensic_findings,
    _pick_amount_column,
    _pick_audit_table,
)


def test_pick_amount_column_excludes_identity_columns():
    # numeric IDs must NOT be picked — auditing them yields garbage findings
    cols = [
        {"name": "sales_order_id", "type": "BIGINT"},
        {"name": "product_id", "type": "BIGINT"},
        {"name": "quantity", "type": "INTEGER"},
    ]
    assert _pick_amount_column(cols) == "quantity"


def test_pick_amount_column_prefers_amount_named():
    cols = [
        {"name": "id", "type": "BIGINT"},
        {"name": "qty", "type": "INTEGER"},
        {"name": "total_amount", "type": "DOUBLE"},
    ]
    assert _pick_amount_column(cols) == "total_amount"


def test_pick_amount_column_none_when_no_numeric():
    assert _pick_amount_column([{"name": "city", "type": "VARCHAR"}]) is None


def test_pick_audit_table_prefers_named_then_numeric():
    tables = {
        "customer": {"columns": [{"name": "name", "type": "VARCHAR"}]},
        "salesorder": {"columns": [{"name": "amount", "type": "DOUBLE"}]},
    }
    assert _pick_audit_table(None, "audit the salesorder data", tables) == "salesorder"
    # no name match → falls back to the table with numeric columns
    assert _pick_audit_table(None, "audit everything", tables) == "salesorder"


def test_forensic_findings_flags_real_anomalies():
    # round-number heavy + a clear 3-sigma outlier + duplicates → findings
    amounts = [1000.0] * 120 + [50.0] * 60 + [9_999_999.0] + [float(i % 7) for i in range(40)]
    findings = _forensic_findings(amounts)
    assert len(findings) >= 1
    tests = {f["evidence_payload"]["test"] for f in findings}
    assert tests & {"round_number", "duplicate_amounts", "z_score_outlier", "benford_first_digit"}
    # every finding has the cert-required shape
    for f in findings:
        assert {"pcaob_standard", "risk_level", "description", "evidence_payload"} <= set(f)


def test_forensic_findings_does_not_over_flag_a_handful_of_values():
    # tiny sample (below thresholds) → no spurious findings
    assert _forensic_findings([10.0, 20.5, 33.1]) == []
