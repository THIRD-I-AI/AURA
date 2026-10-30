"""ADR-002 phase 2a item 1: proven repairs and quarantine.

Every repair in docs/design/ADR-002 "What proven means for each repair" is applied
only when the batch proves it, and its output must equal the ground truth exactly.
Anything that cannot be proven is quarantined with a reason, rows untouched. The
guesses the old templates made (drop a new column, cast a failure to None, default a
removed column to None, rename by list order, rescale by a mean ratio, auto-deploy an
LLM shim) no longer reach the data.

Real DriftDetector, RecoveryLoop and /uasr/heal handler throughout; the database is
an isolated SQLite file, as in tests/test_uasr_heal_reports_truthfully.py. The FastAPI
lifespan is never driven.
"""
from __future__ import annotations

import asyncio
import copy
import os
import random
import sys
import tempfile
import uuid

import pytest
from starlette.requests import Request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import metadata_store.db as _metadata_db  # noqa: E402
from uasr import service  # noqa: E402
from uasr.actuator_agent import SynthesisActuatorAgent  # noqa: E402
from uasr.db import get_session_factory, init_uasr_db  # noqa: E402
from uasr.drift_detector import DriftDetector  # noqa: E402
from uasr.models import (  # noqa: E402
    BatchPayload,
    DiagnosisResult,
    DriftDetectionResult,
    DriftType,
    RecoveryStatus,
    ShimResult,
)
from uasr.recovery_loop import RecoveryLoop, RecoveryLoopConfig  # noqa: E402

SCHEMA = {"customer_id": "int", "amount": "float", "status": "str"}


@pytest.fixture(scope="module", autouse=True)
def _isolated_metadata_db():
    original = (_metadata_db.DATABASE_URL, _metadata_db._engine, _metadata_db._session_factory)
    tmp_path = os.path.join(tempfile.gettempdir(), f"aura_heal2a_{uuid.uuid4().hex[:8]}.db")
    _metadata_db.DATABASE_URL = f"sqlite+aiosqlite:///{tmp_path}"
    _metadata_db._engine = None
    _metadata_db._session_factory = None
    yield
    leaked = _metadata_db._engine  # BUG-008: dispose before restoring, or pytest hangs at exit
    if leaked is not None:
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(leaked.dispose())
        finally:
            loop.close()
    _metadata_db.DATABASE_URL, _metadata_db._engine, _metadata_db._session_factory = original
    try:
        os.remove(tmp_path)
    except OSError:
        pass


@pytest.fixture(autouse=True)
async def _tables(monkeypatch):
    await init_uasr_db()
    # No LLM in CI: the proposal path for an unproven fault falls back to templates.
    monkeypatch.setattr(SynthesisActuatorAgent, "_llm_shim", _no_llm)
    yield


async def _no_llm(self, *a, **k):
    return None


def _rows(n: int, seed: int, **extra_cols):
    rnd = random.Random(seed)
    out = []
    for _ in range(n):
        row = {
            "customer_id": rnd.randint(1, 10_000),
            "amount": round(rnd.uniform(10.0, 500.0), 2),
            "status": rnd.choice(["active", "inactive", "pending"]),
        }
        for name, make in extra_cols.items():
            row[name] = make(rnd)
        out.append(row)
    return out


def _request(tenant: str | None = None) -> Request:
    scope = {"type": "http", "method": "POST", "headers": [], "query_string": b"", "path": "/x"}
    if tenant:
        scope["state"] = {"user": {"org_id": tenant}}
    return Request(scope)


def _source() -> str:
    return f"src_{uuid.uuid4().hex[:8]}"


def _register(source: str, rows, schema=SCHEMA) -> None:
    service._detector.register_baseline(
        source, BatchPayload(source_id=source, columns=list(rows[0]), rows=rows, schema_snapshot=schema))


async def _heal(source: str, rows, schema=None, tenant=None, metadata=None) -> dict:
    req = service.IngestRequest(source_id=source, rows=rows, schema_snapshot=schema, metadata=metadata or {})
    async with get_session_factory()() as db:
        return await service.heal_batch(req, _request(tenant), db=db)


def _accounted(out: dict, sent: list) -> None:
    """Returned + quarantined rows hold every input row exactly once."""
    indices = sorted(q["index"] for q in out["quarantined_rows"])
    assert len(indices) == len(set(indices))
    assert len(out["rows"]) + len(indices) == len(sent)
    for q in out["quarantined_rows"]:
        assert q["row"] == sent[q["index"]], "a quarantined row must come back exactly as sent"
        assert q["reason"]


# ── Rename ────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_rename_by_normalised_name_restores_the_column_exactly():
    source = _source()
    _register(source, _rows(400, 1))
    truth = _rows(300, 2)
    sent = [{("Customer_ID" if k == "customer_id" else k): v for k, v in r.items()} for r in truth]

    out = await _heal(source, copy.deepcopy(sent))

    assert out["healed"] is True and out["quarantined_rows"] == []
    assert out["rows"] == truth
    rename = [r for r in out["repairs"] if r["type"] == "rename"]
    assert rename == [{"type": "rename", "column": "customer_id",
                       "proof": {**rename[0]["proof"], "rule": "normalised_name", "from": "Customer_ID"}}]


@pytest.mark.asyncio
async def test_rename_by_value_fingerprint_restores_the_column_exactly():
    source = _source()
    _register(source, _rows(400, 3))
    truth = _rows(300, 4)
    sent = [{("amt_usd" if k == "amount" else k): v for k, v in r.items()} for r in truth]

    out = await _heal(source, copy.deepcopy(sent))

    assert out["healed"] is True and out["rows"] == truth
    (rename,) = out["repairs"]
    assert rename["type"] == "rename" and rename["column"] == "amount"
    assert rename["proof"]["rule"] == "value_fingerprint" and rename["proof"]["match_ratio"] >= 0.99


@pytest.mark.asyncio
async def test_ambiguous_rename_quarantines_the_batch_untouched():
    # Two removed columns with the same distribution: either new column fits both,
    # so pairing them would be a guess (the old template paired them by list order).
    def price(rnd):
        return round(rnd.uniform(10.0, 500.0), 2)
    schema = {**SCHEMA, "price": "float", "cost": "float"}
    source = _source()
    _register(source, _rows(400, 5, price=price, cost=price), schema)
    truth = _rows(300, 6, price=price, cost=price)
    renamed = {"price": "p_new", "cost": "c_new"}
    sent = [{renamed.get(k, k): v for k, v in r.items()} for r in truth]

    out = await _heal(source, copy.deepcopy(sent))

    assert out["healed"] is False and out["degraded"] is True
    assert out["rows"] == [] and out["repairs"] == []
    _accounted(out, sent)
    assert "ambiguous" in out["quarantined_rows"][0]["reason"]


@pytest.mark.asyncio
async def test_a_removed_column_with_no_match_is_quarantined_not_filled_with_none():
    source = _source()
    _register(source, _rows(400, 7))
    sent = [{k: v for k, v in r.items() if k != "status"} for r in _rows(300, 8)]

    out = await _heal(source, copy.deepcopy(sent))

    assert out["healed"] is False and out["rows"] == []
    _accounted(out, sent)
    assert "'status' is missing" in out["quarantined_rows"][0]["reason"]


# ── Retype ────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_retype_casts_every_value_back_exactly():
    source = _source()
    _register(source, _rows(400, 9))
    truth = _rows(300, 10)
    sent = [{**r, "customer_id": str(r["customer_id"]), "amount": str(r["amount"])} for r in truth]

    out = await _heal(source, copy.deepcopy(sent), schema={**SCHEMA, "customer_id": "str", "amount": "str"})

    assert out["healed"] is True and out["quarantined_rows"] == []
    assert out["rows"] == truth
    assert all(type(r["customer_id"]) is int and type(r["amount"]) is float for r in out["rows"])
    assert {(r["type"], r["column"]) for r in out["repairs"]} == {("retype", "customer_id"), ("retype", "amount")}


@pytest.mark.asyncio
async def test_retype_quarantines_values_that_do_not_round_trip_and_never_nulls_them():
    source = _source()
    _register(source, _rows(400, 11))
    truth = _rows(300, 12)
    sent = [{**r, "customer_id": str(r["customer_id"])} for r in truth]
    bad = {3: "007", 50: "12.5", 120: "n/a", 200: "+42"}
    for i, v in bad.items():
        sent[i]["customer_id"] = v

    out = await _heal(source, copy.deepcopy(sent), schema={**SCHEMA, "customer_id": "str"})

    _accounted(out, sent)
    assert sorted(q["index"] for q in out["quarantined_rows"]) == sorted(bad)
    assert out["rows"] == [r for i, r in enumerate(truth) if i not in bad]
    assert all(r["customer_id"] is not None for r in out["rows"])
    (retype,) = out["repairs"]
    assert retype["proof"]["rows_quarantined"] == len(bad)
    assert out["healed"] is True and out["degraded"] is True


@pytest.mark.asyncio
async def test_retype_to_a_type_with_no_defined_lossless_cast_quarantines_the_batch():
    source = _source()
    schema = {**SCHEMA, "customer_id": "timestamp"}
    _register(source, _rows(400, 13), schema)
    sent = _rows(300, 14)

    out = await _heal(source, copy.deepcopy(sent), schema={**schema, "customer_id": "int"})

    assert out["healed"] is False and out["rows"] == []
    _accounted(out, sent)
    assert "no lossless cast" in out["quarantined_rows"][0]["reason"]


@pytest.mark.parametrize("value,target,expected", [
    ("42", "int", (True, 42)),
    ("007", "int", (False, None)),
    ("+5", "int", (False, None)),
    ("4.0", "int", (False, None)),
    (4.0, "int", (True, 4)),
    ("1.50", "float", (True, 1.5)),
    ("0.10000000000000001", "float", (False, None)),
    ("nan", "float", (False, None)),
    ("true", "bool", (True, True)),
    ("1", "bool", (False, None)),
    (None, "int", (True, None)),
    (True, "int", (False, None)),
])
def test_cast_losslessly_accepts_only_exact_round_trips(value, target, expected):
    from uasr.actuator_agent import cast_losslessly

    assert cast_losslessly(value, target) == expected


# ── New column ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_new_column_is_kept_not_dropped():
    source = _source()
    _register(source, _rows(400, 15))
    sent = _rows(300, 16, region=lambda rnd: rnd.choice(["eu", "us"]))

    out = await _heal(source, copy.deepcopy(sent))

    assert out["healed"] is True and out["rows"] == sent
    assert out["repairs"] == [{"type": "new_column", "column": "region",
                               "proof": {"rule": "schema_evolves", "kept": True, "rows_with_value": 300}}]


# ── Unit / scale ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_unit_scale_fix_restores_values_exactly():
    source = _source()
    _register(source, _rows(400, 17))
    truth = _rows(300, 18)
    sent = [{**r, "amount": r["amount"] * 100} for r in truth]

    out = await _heal(source, copy.deepcopy(sent))

    assert out["healed"] is True and out["quarantined_rows"] == []
    assert out["rows"] == truth
    (rescale,) = out["repairs"]
    assert rescale["type"] == "rescale" and rescale["column"] == "amount"
    assert rescale["proof"]["factor"] == 100.0 and rescale["proof"]["inside_ratio"] >= 0.999


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", [
    lambda v: v * 7,          # a unit factor that is not a power of ten
    lambda v: v + 300.0,      # a location shift: rescaling it would invent data
])
async def test_drift_no_power_of_ten_explains_is_quarantined(fault):
    source = _source()
    _register(source, _rows(400, 19))
    sent = [{**r, "amount": fault(r["amount"])} for r in _rows(300, 20)]

    out = await _heal(source, copy.deepcopy(sent))

    assert out["healed"] is False and out["rows"] == [] and out["repairs"] == []
    _accounted(out, sent)
    assert "no proven repair applies" in out["quarantined_rows"][0]["reason"]


def test_restore_scaled_inverts_the_float_fault_bit_for_bit():
    from uasr.actuator_agent import restore_scaled

    for truth in (0.07, 123.45, 19.99, 0.1, 1e-3):
        assert restore_scaled(truth * 100, 2) == truth
    assert restore_scaled(500, 1) == 50 and isinstance(restore_scaled(500, 1), int)


# ── Detection-only ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_duplicates_are_reported_and_alerted_only():
    source = _source()
    _register(source, _rows(400, 21))
    base = _rows(200, 22)
    sent = base + copy.deepcopy(base[:100])

    out = await _heal(source, copy.deepcopy(sent))

    assert out["rows"] == sent and out["quarantined_rows"] == [] and out["repairs"] == []
    assert [a["type"] for a in out["alerts"]] == ["duplicate"]
    assert out["healed"] is False and out["degraded"] is True


@pytest.mark.asyncio
async def test_a_null_spike_is_detection_only():
    source = _source()
    _register(source, _rows(400, 23))
    sent = [{**r, "amount": None if i % 2 else r["amount"]} for i, r in enumerate(_rows(300, 24))]

    out = await _heal(source, copy.deepcopy(sent))

    assert out["rows"] == sent and out["quarantined_rows"] == []
    assert "null_rate" in [a["type"] for a in out["alerts"]]
    assert out["healed"] is False


# ── Clean data, tenancy ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_clean_batches_raise_no_alarm_and_come_back_healed():
    source = _source()
    _register(source, _rows(400, 25))
    for seed in range(26, 34):
        sent = _rows(300, seed)
        out = await _heal(source, copy.deepcopy(sent))
        assert out["drift_detected"] is False, out
        assert out["healed"] is True and out["rows"] == sent and out["quarantined_rows"] == []


@pytest.mark.asyncio
async def test_a_proof_never_uses_another_tenants_baseline():
    raw = _source()
    service._detector.register_baseline(
        f"tenantA::{raw}", BatchPayload(source_id=raw, columns=list(SCHEMA), rows=_rows(400, 35)))
    sent = [{**r, "amount": r["amount"] * 100} for r in _rows(300, 36)]

    out = await _heal(raw, copy.deepcopy(sent), tenant="tenantB")

    # tenantB has no baseline: its first batch becomes the reference, nothing is rescaled.
    assert out["rows"] == sent and out["repairs"] == []


# ── Removed guesses no longer auto-apply ──────────────────────────────────────

def _diag() -> DiagnosisResult:
    return DiagnosisResult(drift_event_id="e", root_cause="r", hypothesis="h", suggested_action="a")


@pytest.mark.asyncio
@pytest.mark.parametrize("drift_type,vector,template", [
    ("schema", {"type": "schema_change", "added": ["region"], "removed": []}, "Column Filter"),
    ("schema", {"type": "type_change", "old_types": {"v": "int"}, "new_types": {"v": "str"}}, "Type Cast Recovery"),
    ("schema", {"type": "schema_change", "added": [], "removed": ["v"]}, "Missing Column Recovery"),
    ("schema", {"type": "schema_change", "added": ["a"], "removed": ["b"]}, "Column Rename Mapping"),
    ("statistical", {"affected_columns": ["v"], "max_kl": 0.2, "threshold_zeta": 0.15,
                     "col_stats": {"v": {"baseline_mean": 10.0, "batch_mean": 600.0}}}, "Unit Rescale"),
    ("statistical", {"affected_columns": ["v"], "max_kl": 2.0, "threshold_zeta": 0.15,
                     "col_stats": {"v": {"baseline_mean": 10.0, "batch_mean": 10.5, "baseline_std": 1.0}}},
     "Outlier Clipping"),
])
async def test_unprovable_templates_are_proposals_held_for_review(drift_type, vector, template):
    from agents.base import AgentContext

    result = await SynthesisActuatorAgent().execute(AgentContext(
        user_prompt="p", task_description="t",
        metadata={"diagnosis": _diag().model_dump(), "drift_type": drift_type, "drift_vector": vector,
                  "recovery_id": "r"}))
    shim: ShimResult = result.artifacts["shim"]
    assert template in shim.shim_code
    assert shim.requires_human_review is True and "ADR-002" in (shim.escalation_reason or "")

    loop = RecoveryLoop(detector=DriftDetector(), config=RecoveryLoopConfig(risk_tiered=False))
    drift = DriftDetectionResult(source_id="s", batch_id="b", drift_detected=True,
                                 drift_type=DriftType(drift_type), severity="medium", drift_vector=vector)
    out = await loop.run(drift, BatchPayload(source_id="s", rows=[{"v": 1}]))
    assert out.status == RecoveryStatus.PENDING_APPROVAL and not out.shim.deployed
    assert loop.get_deployed_shims("s") == []


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["llm", "fallback", "cross_source_borrowed"])
async def test_llm_and_borrowed_shims_never_auto_apply(method):
    loop = RecoveryLoop(detector=DriftDetector(), config=RecoveryLoopConfig(risk_tiered=False))
    drift = DriftDetectionResult(source_id="s", batch_id="b", drift_detected=True,
                                 drift_type=DriftType.SEMANTIC, severity="low")
    shim = ShimResult(recovery_id="r", shim_code="def transform(rows):\n    return rows\n",
                      generation_method=method)
    assert loop._should_deploy(shim, drift) is False and loop._must_hold(shim) is True


@pytest.mark.asyncio
async def test_heal_never_applies_a_shim_the_recovery_loop_proposes(monkeypatch):
    # The old /uasr/heal applied whatever the loop deployed; a dropped-column shim
    # deployed by it reached the caller's rows.
    source = _source()
    _register(source, _rows(400, 37))
    sent = [{k: v for k, v in r.items() if k != "status"} for r in _rows(300, 38)]

    out = await _heal(source, copy.deepcopy(sent))

    assert out["shim_deployed"] is False and out["rows"] == []
    assert out["status"] == RecoveryStatus.PENDING_APPROVAL.value
    assert service._loop.get_deployed_shims(source) == []
    _accounted(out, sent)


# ── Standing shims (review of ea53a59) ─────────────────────────────────────────

# What the pre-ADR-002 Column Filter template did to a new column: drop it.
_LEGACY_COLUMN_FILTER = (
    "# UASR Shim - Column Filter\n"
    "def transform(rows):\n"
    "    return [{k: v for k, v in r.items() if k != 'coupon'} for r in rows]\n"
)


async def _deployed_record(source: str, shim: str, decided_by: str | None) -> None:
    from uasr.models import DriftEvent, RecoveryRecord
    rec_id = uuid.uuid4().hex[:16]
    async with get_session_factory()() as db:
        db.add(DriftEvent(id=rec_id + "_d", source_id=source, drift_type="schema"))
        db.add(RecoveryRecord(
            id=rec_id, drift_event_id=rec_id + "_d", source_id=source,
            status=RecoveryStatus.DEPLOYED.value, shim_code=shim, decided_by=decided_by,
        ))
        await db.commit()


@pytest.mark.asyncio
async def test_a_legacy_auto_deployed_template_is_not_reloaded_as_a_standing_shim():
    from uasr.recovery_persistence import load_deployed_shims
    legacy, approved = _source(), _source()
    approved_shim = "def transform(rows):\n    return rows\n"
    await _deployed_record(legacy, _LEGACY_COLUMN_FILTER, decided_by=None)
    await _deployed_record(approved, approved_shim, decided_by="alice")
    try:
        await service._restore_standing_shims()
        assert service._loop.get_deployed_shims(legacy) == []
        assert service._loop.get_deployed_shims(approved) == [approved_shim]
        # The shared-registry path (BUG-467) reads the same filter.
        assert await load_deployed_shims(legacy) == []
        assert await load_deployed_shims(approved) == [approved_shim]

        # So the new column survives and the batch is honestly reported.
        _register(legacy, _rows(400, 40))
        sent = _rows(300, 41, coupon=lambda rnd: rnd.choice(["A", "B", "C"]))
        out = await _heal(legacy, copy.deepcopy(sent))
        assert all("coupon" in r for r in out["rows"])
        assert out.get("shims_applied", 0) == 0
    finally:
        service._loop.set_deployed_shims(legacy, [])
        service._loop.set_deployed_shims(approved, [])


@pytest.mark.parametrize("shim", [
    # a dedupe / filter shim that drops rows (and edits its input in place)
    "def transform(rows):\n"
    "    for r in rows:\n"
    "        r['amount'] = 0.0\n"
    "    return rows[5:]\n",
    # one that adds rows
    "def transform(rows):\n    return rows + rows[:3]\n",
])
@pytest.mark.asyncio
async def test_a_standing_shim_that_changes_the_row_count_quarantines_the_batch_as_sent(shim):
    source = _source()
    _register(source, _rows(400, 42))
    sent = _rows(300, 43)
    service._loop.set_deployed_shims(source, [shim])
    try:
        out = await _heal(source, copy.deepcopy(sent))
    finally:
        service._loop.set_deployed_shims(source, [])

    assert out["healed"] is False and out["degraded"] is True
    assert out["rows"] == [] and len(out["quarantined_rows"]) == len(sent)
    _accounted(out, sent)
    assert "row(s) into" in out["quarantined_rows"][0]["reason"]


@pytest.mark.asyncio
async def test_a_standing_shim_that_keeps_the_row_count_is_still_applied():
    source = _source()
    _register(source, _rows(400, 44))
    sent = _rows(300, 45)
    service._loop.set_deployed_shims(source, ["def transform(rows):\n    return list(rows)\n"])
    try:
        out = await _heal(source, copy.deepcopy(sent))
    finally:
        service._loop.set_deployed_shims(source, [])

    assert out["healed"] is True and out["shims_applied"] == 1
    assert out["rows"] == sent
