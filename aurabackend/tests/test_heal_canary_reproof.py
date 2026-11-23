"""ADR-002 canary: a batch that cannot prove a watched repair again is no evidence it
is wrong (steady-state seeds 194 and 336).

The steady-state lane rolled back two correct repairs: a str->float retype on a
lognormal column (seed 194) and a rename onto one (seed 336). A later clean batch
failed to prove them again -- the cast-back values and the renamed column's
fingerprint both pass a moment check against the baseline's sample std, which one far
value inflates -- and ``judge`` read "the repair's column failed" as a regression. The
rollback then blocked the correct mapping on every later batch: a recall bug that the
canary turned permanent.

The rule now: a batch that cannot prove the repair is still quarantined (the repair is
never applied blindly), but only evidence AGAINST the repair -- its output on the
column provably not the baseline's, or values not of the column's kind -- rolls it
back. Real DriftDetector, RecoveryLoop, RepairCanary and /uasr/heal handler on an
isolated SQLite file; the FastAPI lifespan is not driven, and no LLM is called.
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
from uasr import repair_canary, service  # noqa: E402
from uasr.actuator_agent import SynthesisActuatorAgent, fingerprint  # noqa: E402
from uasr.db import get_session_factory, init_uasr_db  # noqa: E402
from uasr.drift_detector import DriftDetector  # noqa: E402
from uasr.models import BatchPayload  # noqa: E402
from uasr.recovery_loop import RecoveryLoop, RecoveryLoopConfig  # noqa: E402
from uasr.repair_canary import INCONCLUSIVE, REGRESSED, RepairCanary, judge  # noqa: E402

N = 3
SCHEMA = {"customer_id": "int", "amount": "float", "status": "str"}
FAR = 2000.0  # one far value: it inflates the baseline's sample std ~4x


def _rows(n: int, seed: int, far: int = 0, start: int = 0) -> list:
    rnd = random.Random(seed)
    rows = [{"customer_id": start + i + 1, "amount": round(rnd.uniform(10.0, 100.0), 2),
             "status": rnd.choice(["active", "inactive", "pending"])} for i in range(n)]
    for i in range(far):
        rows[i * 7 % n]["amount"] = FAR
    return rows


@pytest.fixture(scope="module", autouse=True)
def _isolated_metadata_db():
    original = (_metadata_db.DATABASE_URL, _metadata_db._engine, _metadata_db._session_factory)
    tmp_path = os.path.join(tempfile.gettempdir(), f"aura_canary_reproof_{uuid.uuid4().hex[:8]}.db")
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


@pytest.fixture(autouse=True)
async def _uasr(monkeypatch):
    await init_uasr_db()
    detector = DriftDetector()
    monkeypatch.setattr(service, "_detector", detector)
    monkeypatch.setattr(service, "_loop", RecoveryLoop(detector=detector, config=RecoveryLoopConfig()))
    monkeypatch.setattr(service, "_canary", RepairCanary(N))
    monkeypatch.setattr(service, "_mapek_worker", None)
    monkeypatch.setattr(service, "_SHARED_SHIM_REGISTRY", False)
    monkeypatch.setattr(SynthesisActuatorAgent, "_llm_shim", _no_llm)
    yield


def _request() -> Request:
    return Request({"type": "http", "method": "POST", "headers": [], "query_string": b"", "path": "/x"})


async def _heal(source: str, rows: list) -> dict:
    req = service.IngestRequest(source_id=source, rows=copy.deepcopy(rows))
    async with get_session_factory()() as db:
        return await service.heal_batch(req, _request(), db=db)


def _register() -> str:
    source = f"reproof_{uuid.uuid4().hex[:8]}"
    service._detector.register_baseline(source, BatchPayload(
        source_id=source, columns=list(SCHEMA), rows=_rows(400, 1, far=1), schema_snapshot=SCHEMA))
    return source


def _sigs(out: dict, key: str) -> set:
    return {(w["type"], w["column"], w["param"]) for w in out["canary"][key]}


def _renamed(rows: list) -> list:
    return [{("amt_x" if k == "amount" else k): v for k, v in r.items()} for r in rows]


def _as_text(rows: list) -> list:
    return [{**r, "amount": repr(r["amount"])} for r in rows]


def test_a_clean_batch_can_fail_the_moment_check_the_fingerprint_needs():
    """The premise: without the far value the clean batch's sample std is a quarter of
    the baseline's, so the fingerprint cannot prove the rename on it."""
    det = DriftDetector()
    det.register_baseline("p", BatchPayload(source_id="p", columns=list(SCHEMA), rows=_rows(400, 1, far=1)))
    dist = det.baseline_distributions("p")["amount"]
    with_far = [r["amount"] for r in _rows(300, 2, far=1)]
    without = [r["amount"] for r in _rows(300, 3)]
    assert fingerprint(with_far, dist) == (1.0, True)
    assert fingerprint(without, dist) == (1.0, False)


# ── judge ──────────────────────────────────────────────────────────────────────

RENAME = ("rename", "amount", "amt_x")


def _evidence(contradicts=None) -> dict:
    ev = {"failed_columns": {"amount"}, "refused": set(), "proven": set(), "retype_seen": None,
          "scale_seen": None, "columns": {"amt_x", "customer_id"}}
    if contradicts is not None:
        ev["contradicts"] = contradicts
    return ev


def test_judge_rolls_back_only_on_evidence_against_the_repair():
    assert judge(RENAME, _evidence(lambda sig: True)) == REGRESSED
    assert judge(RENAME, _evidence(lambda sig: False)) == INCONCLUSIVE
    # Evidence without the contradiction test (a caller that predates it) keeps the
    # old, stricter reading.
    assert judge(RENAME, _evidence()) == REGRESSED
    # No drift at all is still a clean batch for every watch.
    assert judge(RENAME, None) == repair_canary.CLEAN


# ── RecoveryLoop._contradicts ─────────────────────────────────────────────────

def _baseline():
    det = DriftDetector()
    det.register_baseline("c", BatchPayload(source_id="c", columns=list(SCHEMA), rows=_rows(400, 1, far=1)))
    return det.baseline_distributions("c")


@pytest.mark.parametrize("sig, make, expected", [
    # A clean column under the new name, cast back, or divided by the factor: no evidence.
    (("rename", "amount", "amt_x"), lambda rs: _renamed(rs), False),
    (("retype", "amount", "str->float"), lambda rs: _as_text(rs), False),
    (("rescale", "amount", "100.0"), lambda rs: [{**r, "amount": r["amount"] * 100} for r in rs], False),
    # The repair's output provably is not the column (x7 on top: the canary's case).
    (("rename", "amount", "amt_x"), lambda rs: _renamed([{**r, "amount": r["amount"] * 7} for r in rs]), True),
    (("retype", "amount", "str->float"), lambda rs: _as_text([{**r, "amount": r["amount"] * 2.5} for r in rs]), True),
    (("rescale", "amount", "100.0"), lambda rs: [{**r, "amount": r["amount"] * 700} for r in rs], True),
    # Values that are not of the column's kind at all.
    (("rename", "amount", "amt_x"), lambda rs: _renamed([{**r, "amount": f"#{i}"} for i, r in enumerate(rs)]), True),
    (("retype", "amount", "str->float"), lambda rs: [{**r, "amount": "n/a"} for r in rs], True),
])
def test_contradicts_needs_evidence_against_the_repair(sig, make, expected):
    rows = make(_rows(300, 3))
    assert RecoveryLoop._contradicts(rows, _baseline(), sig) is expected


def test_a_repair_the_batch_does_not_exercise_is_not_contradicted():
    # The old name is back: the rename has nothing to say about this batch.
    assert RecoveryLoop._contradicts(_rows(300, 3), _baseline(), RENAME) is False
    assert RecoveryLoop._contradicts([], _baseline(), ("rescale", "amount", "100.0")) is False


# ── through /uasr/heal ────────────────────────────────────────────────────────

async def test_a_rename_a_clean_batch_cannot_prove_is_quarantined_not_rolled_back():
    source = _register()
    out = await _heal(source, _renamed(_rows(300, 2, far=1, start=1000)))
    assert out["healed"] is True and _sigs(out, "opened") == {RENAME}

    # Seed 336: a clean batch the fingerprint cannot prove. Its rows are held (the
    # rename is not applied blindly), and the watch is not rolled back.
    out = await _heal(source, _renamed(_rows(300, 3, start=2000)))
    assert out["healed"] is False and len(out["quarantined_rows"]) == 300 and not out["rows"]
    assert not _sigs(out, "rolled_back")
    assert not service._canary.blocked(source)
    assert [w.signature for w in service._canary.watches(source)] == [RENAME]
    assert service._canary.watches(source)[0].batches_seen == 0  # inconclusive: no progress

    # Batches that do prove it count, and N of them keep it.
    for k in range(N):
        out = await _heal(source, _renamed(_rows(300, 10 + k, far=1, start=3000 + 300 * k)))
        assert out["healed"] is True, out["reason"]
    assert _sigs(out, "kept") == {RENAME}


async def test_a_rename_the_batch_contradicts_is_still_rolled_back():
    source = _register()
    await _heal(source, _renamed(_rows(300, 2, far=1, start=1000)))
    x7 = [{**r, "amount": r["amount"] * 7} for r in _rows(300, 4, far=0, start=2000)]
    out = await _heal(source, _renamed(x7))
    assert out["healed"] is False and not out["rows"]
    assert _sigs(out, "rolled_back") == {RENAME}
    assert RENAME in service._canary.blocked(source)


async def test_a_retype_a_clean_batch_cannot_prove_is_quarantined_not_rolled_back():
    """Seed 194: str->float on a heavy-tailed column. The cast-back values of a clean
    batch fail the retype proof's moment check; the batch is held, the watch stays."""
    retype = ("retype", "amount", "str->float")
    source = _register()
    out = await _heal(source, _as_text(_rows(300, 2, far=1, start=1000)))
    assert out["healed"] is True and _sigs(out, "opened") == {retype}

    out = await _heal(source, _as_text(_rows(300, 3, start=2000)))
    assert out["healed"] is False and not out["rows"]
    assert not _sigs(out, "rolled_back") and not service._canary.blocked(source)
    assert [w.signature for w in service._canary.watches(source)] == [retype]

    # x2.5 on top of the text: the cast-back values provably are not the column's.
    scaled = [{**r, "amount": r["amount"] * 2.5} for r in _rows(300, 5, start=3000)]
    out = await _heal(source, _as_text(scaled))
    assert out["healed"] is False and not out["rows"]
    assert _sigs(out, "rolled_back") == {retype}
