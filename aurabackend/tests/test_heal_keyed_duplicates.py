"""ADR-002 "exact duplicates on the declared key": a source that declares a unique key
(the baseline's ``metadata.key_columns``) gets the duplicates alert for every key value
a batch repeats, and for every key value an earlier page of the same run
(``metadata.run_id``) already delivered -- every time, with no statistical floor.

Keyless sources keep the BUG-574 rules (identifier columns exact, otherwise the
baseline's own repeat rate). Real DriftDetector, RecoveryLoop and /uasr/heal handler
on an isolated SQLite file; the FastAPI lifespan is not driven, and no LLM is called.
"""
from __future__ import annotations

import asyncio
import copy
import os
import random
import sys
import tempfile
import uuid

import numpy as np
import pytest
from starlette.requests import Request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import metadata_store.db as _metadata_db  # noqa: E402
from uasr import service  # noqa: E402
from uasr.actuator_agent import SynthesisActuatorAgent  # noqa: E402
from uasr.db import get_session_factory, init_uasr_db  # noqa: E402
from uasr.drift_detector import DriftDetector, _key_hashes, _RunKeys  # noqa: E402
from uasr.models import BatchPayload, DriftType  # noqa: E402
from uasr.recovery_loop import RecoveryLoop, RecoveryLoopConfig  # noqa: E402
from uasr.repair_canary import RepairCanary  # noqa: E402

COLUMNS = ["order_no", "amount", "status"]


def _rows(n: int, seed: int, start: int = 0) -> list:
    rnd = random.Random(seed)
    # order_no is an opaque code, not a value the detector could take for an id on its own.
    return [{"order_no": f"K{(start + i) * 7919 % 1_000_003:07d}", "amount": round(rnd.uniform(5, 500), 2),
             "status": rnd.choice(["new", "paid", "shipped"])} for i in range(n)]


def _detector(key=("order_no",), baseline_dup: int = 0) -> DriftDetector:
    det = DriftDetector()
    rows = _rows(500, 1)
    rows += [dict(rows[i]) for i in range(baseline_dup)]
    meta = {"key_columns": list(key)} if key else {}
    det.register_baseline("s", BatchPayload(source_id="s", columns=COLUMNS, rows=rows, metadata=meta))
    return det


def _dup(det: DriftDetector, rows: list, *, record: bool = False, **meta) -> dict:
    batch = BatchPayload(source_id="s", columns=COLUMNS, rows=rows, metadata=meta,
                         batch_id=meta.pop("batch_id", None) or uuid.uuid4().hex[:12])
    r = det.detect(batch, record_state=record)
    return (r.drift_vectors or {}).get(DriftType.DUPLICATE.value) or {}


# ── one batch ─────────────────────────────────────────────────────────────────

def test_one_repeated_key_in_a_large_batch_alerts():
    det = _detector()
    rows = _rows(1000, 2, start=10_000)
    rows[500] = {**rows[500], "order_no": rows[3]["order_no"]}  # same key, other values
    found = _dup(det, rows)
    assert found["duplicate_rows"] == 1 and found["key_columns"] == ["order_no"]
    assert "declared key" in found["details"]


def test_a_repeated_key_alerts_even_when_the_baseline_held_one():
    """The declaration, not the baseline's history, makes the key unique: a baseline
    with a repeated key used to fall back to the statistical bound."""
    det = _detector(baseline_dup=3)
    rows = _rows(300, 2, start=10_000)
    rows.append(dict(rows[0]))
    assert _dup(det, rows)["duplicate_rows"] == 1


def test_every_batch_that_repeats_a_key_alerts():
    det = _detector()
    for seed in range(5):
        rows = _rows(200, seed, start=10_000 + 1000 * seed)
        rows.append(dict(rows[7]))
        assert _dup(det, rows, record=True)["duplicate_rows"] == 1, seed


def test_null_keys_are_not_duplicates_of_each_other():
    det = _detector()
    rows = _rows(300, 2, start=10_000)
    rows[0] = {**rows[0], "order_no": None}
    rows[1] = {**rows[1], "order_no": None}
    assert not _dup(det, rows)


def test_a_clean_keyed_batch_does_not_alert():
    det = _detector()
    for seed in range(20):
        assert not _dup(det, _rows(400, seed, start=10_000 + 1000 * seed), record=True), seed


# ── across the pages of one run ───────────────────────────────────────────────

def test_a_key_an_earlier_page_of_the_run_delivered_alerts():
    det = _detector()
    page1 = _rows(300, 2, start=10_000)
    page2 = _rows(300, 3, start=10_300)
    assert not _dup(det, page1, record=True, run_id="r1", batch_id="p1")
    page2[10] = {**page2[10], "order_no": page1[42]["order_no"]}
    found = _dup(det, page2, record=True, run_id="r1", batch_id="p2")
    assert found["keys_seen_earlier_in_run"] == 1 and found["duplicate_rows"] == 0
    assert "already delivered earlier in run 'r1'" in found["details"]
    # The sandbox's re-check of the same page (record_state=False, same batch id) sees
    # the same repeat -- and not the page's own keys.
    assert _dup(det, page2, run_id="r1", batch_id="p2")["keys_seen_earlier_in_run"] == 1
    # A page sent again (a retry) is not its own duplicate.
    assert not _dup(det, page1, record=True, run_id="r1", batch_id="p1")


def test_keys_of_another_run_or_without_a_run_are_not_repeats():
    det = _detector()
    page1 = _rows(300, 2, start=10_000)
    assert not _dup(det, page1, record=True, run_id="r1", batch_id="p1")
    assert not _dup(det, page1, record=True, run_id="r2", batch_id="q1")  # the next run
    assert not _dup(det, page1, record=True)  # no run declared: the batch alone


def test_the_page_that_completes_the_run_is_checked_and_then_the_run_is_over():
    det = _detector()
    page1 = _rows(300, 2, start=10_000)
    last = _rows(300, 3, start=10_300) + [dict(page1[5])]
    assert not _dup(det, page1, record=True, run_id="r1", batch_id="p1")
    found = _dup(det, last, record=True, run_id="r1", batch_id="p2", complete_run=True)
    assert found["keys_seen_earlier_in_run"] == 1
    # Its own re-check still sees the run ...
    assert _dup(det, last, run_id="r1", batch_id="p2", complete_run=True)["keys_seen_earlier_in_run"] == 1
    # ... and the next page starts afresh, even under the same run id.
    assert not _dup(det, page1, record=True, run_id="r1", batch_id="p3")


def test_the_run_memory_is_bounded():
    keys = _RunKeys(max_keys=1000)
    h = lambda lo, hi: _key_hashes([f"k{i}" for i in range(lo, hi)])  # noqa: E731
    assert keys.repeats("a", "r", "a1", h(0, 600), record=True, end=False) == 0
    assert keys.repeats("b", "r", "b1", h(0, 300), record=True, end=False) == 0
    assert len(keys) == 900
    # Source c needs room: the least recently used run (a) goes first.
    assert keys.repeats("c", "r", "c1", h(0, 500), record=True, end=False) == 0
    assert len(keys) <= 1000 and keys.repeats("a", "r", "a2", h(0, 10), record=False, end=False) == 0
    # A run that alone outgrows the cap keeps what it has and still checks against it.
    big = _RunKeys(max_keys=100)
    assert big.repeats("s", "r", "p1", h(0, 150), record=True, end=False) == 0
    assert len(big) == 100
    assert big.repeats("s", "r", "p2", h(0, 150), record=True, end=False) == 100


def test_key_hashes_are_unique_and_sorted():
    got = _key_hashes(["x", "y", "x"])
    assert len(got) == 2 and got.dtype == np.uint64 and list(got) == sorted(got)


# ── through /uasr/heal ────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def _isolated_metadata_db():
    original = (_metadata_db.DATABASE_URL, _metadata_db._engine, _metadata_db._session_factory)
    tmp_path = os.path.join(tempfile.gettempdir(), f"aura_keyed_dups_{uuid.uuid4().hex[:8]}.db")
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


async def _no_llm(self, *a, **k):
    return None


@pytest.fixture
async def _uasr(_isolated_metadata_db, monkeypatch):
    await init_uasr_db()
    detector = DriftDetector()
    monkeypatch.setattr(service, "_detector", detector)
    monkeypatch.setattr(service, "_loop", RecoveryLoop(detector=detector, config=RecoveryLoopConfig()))
    monkeypatch.setattr(service, "_canary", RepairCanary(3))
    monkeypatch.setattr(service, "_mapek_worker", None)
    monkeypatch.setattr(service, "_SHARED_SHIM_REGISTRY", False)
    monkeypatch.setattr(SynthesisActuatorAgent, "_llm_shim", _no_llm)
    source = f"keyed_{uuid.uuid4().hex[:8]}"
    detector.register_baseline(source, BatchPayload(
        source_id=source, columns=COLUMNS, rows=_rows(500, 1), metadata={"key_columns": ["order_no"]}))
    yield source


async def _heal(source: str, rows: list, **meta) -> dict:
    req = service.IngestRequest(source_id=source, rows=copy.deepcopy(rows), metadata=meta,
                                batch_id=meta.pop("batch_id", ""))
    async with get_session_factory()() as db:
        return await service.heal_batch(
            req, Request({"type": "http", "method": "POST", "headers": [], "query_string": b"", "path": "/x"}),
            db=db)


def _types(out: dict) -> set:
    return {a.get("type") for a in out.get("alerts") or []}


async def test_a_clean_paged_run_alerts_on_no_page(_uasr):
    source = _uasr
    for k in range(4):
        meta = {"run_id": "run-1", "batch_id": f"run-1:{k}"}
        if k == 3:
            meta.update(complete_run=True, run_rows=1200)
        out = await _heal(source, _rows(300, 10 + k, start=20_000 + 300 * k), **meta)
        assert out["drift_detected"] is False and out["healed"] is True and not out["alerts"], (k, out["reason"])


async def test_a_page_repeating_a_key_of_an_earlier_page_gets_the_duplicates_alert(_uasr):
    source = _uasr
    page1 = _rows(300, 10, start=30_000)
    out = await _heal(source, page1, run_id="run-2", batch_id="run-2:0")
    assert not out["alerts"]
    page2 = _rows(300, 11, start=30_300)
    page2[0] = {**page2[0], "order_no": page1[100]["order_no"]}
    out = await _heal(source, page2, run_id="run-2", batch_id="run-2:1", complete_run=True, run_rows=600)
    assert "duplicate" in _types(out)
    # Detection only (ADR-002): nothing is dropped or changed, and the run is not "healed".
    assert out["healed"] is False and out["rows"] == page2 and not out["quarantined_rows"]


async def test_a_batch_repeating_a_key_gets_the_duplicates_alert_every_time(_uasr):
    source = _uasr
    for k in range(3):
        rows = _rows(300, 20 + k, start=40_000 + 300 * k)
        rows.append(dict(rows[k]))
        out = await _heal(source, rows)
        assert "duplicate" in _types(out) and out["healed"] is False, k


async def test_the_sandbox_re_check_of_a_repaired_page_is_not_its_own_duplicate(_uasr):
    """A page with a repairable fault is re-checked by the proof (record_state=False)
    after the detector recorded its keys; the re-check must not find them as keys an
    earlier page delivered (it used to run under the id '<batch>_proof')."""
    source = _uasr
    out = await _heal(source, _rows(300, 30, start=50_000), run_id="run-3", batch_id="run-3:0")
    assert not out["alerts"]
    page2 = [{("amount_usd" if k == "amount" else k): v for k, v in r.items()}
             for r in _rows(300, 31, start=50_300)]
    out = await _heal(source, page2, run_id="run-3", batch_id="run-3:1")
    assert out["healed"] is True and [r["type"] for r in out["repairs"]] == ["rename"], out["reason"]
    assert not out["alerts"]
