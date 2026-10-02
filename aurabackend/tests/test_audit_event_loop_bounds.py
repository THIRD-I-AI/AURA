"""BUG-300: the unauthenticated /audit/sth and /audit/inclusion endpoints parsed whole
audit-log files and built Merkle trees on the event loop.

BUG-301: POST /audit/financial accepted input lists of any length and fingerprinted
them on the event loop."""
from __future__ import annotations

import asyncio
import threading

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

import shared.audit_log as audit_log
from counterfactual_service import main as m


def test_sth_builds_the_merkle_root_off_the_event_loop(monkeypatch):
    loop_thread = threading.get_ident()
    seen = {}

    def _root(day, service_tag=None):
        seen["thread"] = threading.get_ident()
        return None

    monkeypatch.setattr(audit_log, "daily_merkle_root", _root)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(m.get_sth(day="20260101"))

    assert exc.value.status_code == 404
    assert seen["thread"] != loop_thread


def test_inclusion_proof_searches_off_the_event_loop(monkeypatch):
    seen = {}

    def _proof(record_hash, day=None, service_tag=None):
        seen["thread"] = threading.get_ident()
        return None

    monkeypatch.setattr(audit_log, "inclusion_proof_for_record", _proof)

    async def call():
        seen["loop_thread"] = threading.get_ident()
        return await m.get_inclusion_proof("a" * 64, day=None)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(call())

    assert exc.value.status_code == 404
    assert seen["thread"] != seen["loop_thread"]


@pytest.mark.parametrize("record_hash", ["not-a-hash", "a" * 63, "g" * 64, "../../etc/passwd"])
def test_a_value_that_is_not_a_record_hash_never_reaches_the_log(monkeypatch, record_hash):
    def _proof(*a, **k):
        raise AssertionError("the audit log must not be read for a malformed hash")

    monkeypatch.setattr(audit_log, "inclusion_proof_for_record", _proof)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(m.get_inclusion_proof(record_hash, day=None))

    assert exc.value.status_code == 400


@pytest.mark.parametrize("day", ["2026-01-01", "../20260101", "x" * 8, ""])
def test_a_malformed_day_is_rejected(monkeypatch, day):
    monkeypatch.setattr(audit_log, "inclusion_proof_for_record",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not be read")))

    with pytest.raises(HTTPException) as exc:
        asyncio.run(m.get_inclusion_proof("a" * 64, day=day))

    assert exc.value.status_code == 400


def test_financial_audit_input_lists_are_bounded():
    too_many = [{"amount": 1000}] * (m.MAX_AUDIT_ROWS + 1)

    for field in ("ledger", "purchase_orders", "invoices", "journal_entries",
                  "historical_reports", "goods_receipts"):
        with pytest.raises(ValidationError):
            m.FinancialAuditRequest(tenant_id="t", **{field: too_many})

    assert m.FinancialAuditRequest(tenant_id="t", journal_entries=[{"amount": 1}]).journal_entries
