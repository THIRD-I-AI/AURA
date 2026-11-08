"""ADR-002 accuracy: BUG-573 (column roles) and BUG-574 (duplicates).

BUG-573: identifier and date columns were compared like measurements. A sequential
id lies past the baseline's range on every new batch and read as statistical
drift; text ids and dates made every row a new token for the semantic embedding.
604 of the benchmark's 1000 clean batches alarmed. Each column now gets a role
when its baseline is registered; identifiers and dates skip statistical and
semantic drift and are checked for what can go wrong with them instead (kind,
format, nulls, uniqueness).

BUG-574: the duplicate floor sat above small injections (15 copies in 300 rows).
A baseline with no duplicate whose rows are unique by construction now makes any
duplicate an alert; a baseline with duplicates keeps a statistical test, measured
here at zero false alarms over 1000 clean trials.

Real DriftDetector and the real /uasr/heal handler (through the accuracy
benchmark's own harness) on an isolated SQLite file; the FastAPI lifespan is
never driven.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import os
import random
import sys
import tempfile
import uuid

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import metadata_store.db as _metadata_db  # noqa: E402
from tests.accuracy.faults import build_clean, build_trial  # noqa: E402
from tests.accuracy.harness import CORRECT, classify, classify_clean, heal  # noqa: E402
from uasr.actuator_agent import SynthesisActuatorAgent  # noqa: E402
from uasr.db import init_uasr_db  # noqa: E402
from uasr.drift_detector import DriftDetector  # noqa: E402
from uasr.models import BatchPayload, DriftType  # noqa: E402
from uasr.reflector_agent import DiagnosticReflectorAgent  # noqa: E402
from uasr.state_store import SourceState, SqliteStateStore  # noqa: E402


def _rows(n: int, start: int, rnd: random.Random, with_name: bool = True):
    """One table of every role: a sequential id, a keyed id, a text id, a date,
    and measures."""
    rows = _all_rows(n, start, rnd)
    return rows if with_name else [{k: v for k, v in r.items() if k != "name"} for r in rows]


def _all_rows(n: int, start: int, rnd: random.Random):
    day0 = dt.date(2025, 3, 1).toordinal()
    return [{
        "order_id": start + i,
        "customer_key": 1 + (7919 * (start + i) * 104729) % 9_999_991,
        "ref": f"ORD-{start + i:07d}",
        "created_on": dt.date.fromordinal(day0 + rnd.randrange(90)).isoformat(),
        "amount": round(rnd.uniform(5.0, 900.0), 2),
        "qty": rnd.randint(1, 9),
        "status": rnd.choice(["new", "paid", "shipped"]),
        "name": rnd.choice(["Ann Lee", "Bo Chen", "Cy Diaz"]) + f" {rnd.choice('ABCDEFGH')}",
    } for i in range(n)]


SCHEMA = {"order_id": "int", "customer_key": "int", "ref": "str", "created_on": "str",
          "amount": "float", "qty": "int", "status": "str", "name": "str"}


def _payload(rows, bid="b", snapshot=None, metadata=None) -> BatchPayload:
    return BatchPayload(source_id="s", batch_id=bid, columns=list(rows[0]), rows=rows,
                        schema_snapshot=snapshot, metadata=metadata or {})


def _detector(n0=800, seed=0, store=None, metadata=None, with_name=True):
    rnd = random.Random(seed)
    det = DriftDetector(state_store=store)
    det.register_baseline("s", _payload(_rows(n0, 1, rnd, with_name), "base", metadata=metadata))
    return det, rnd, n0 + 1


# ── BUG-573: roles ─────────────────────────────────────────────────────────────


def test_bug573_roles_are_inferred_from_the_baseline_values():
    det, _, _ = _detector()
    base = det.baseline_distributions("s")
    assert base["order_id"].role == "identifier" and base["order_id"].value_bound is None
    assert base["customer_key"].role == "identifier" and base["customer_key"].value_bound is not None
    assert base["ref"].role == "identifier" and base["ref"].value_format == ["A-9"]
    assert base["created_on"].role == "date" and base["created_on"].value_format == ["9999-99-99"]
    for measure in ("amount", "qty", "status", "name"):
        assert base[measure].role == "measure", measure


def test_bug573_measures_are_never_taken_for_identifiers():
    rnd = random.Random(3)
    det = DriftDetector()
    rows = [{"cents": rnd.randint(0, 50_000), "price": round(rnd.uniform(1, 9999), 3),
             "flag": rnd.random() < 0.5, "few": rnd.randint(1, 10_000)} for _ in range(60)]
    det.register_baseline("s", _payload(rows, snapshot={"cents": "int", "price": "float",
                                                        "flag": "bool", "few": "int"}))
    base = det.baseline_distributions("s")
    # 60 unique ints on a sparse range are too few to call a key; floats and
    # booleans never are.
    assert {c: d.role for c, d in base.items()} == {
        "cents": "measure", "price": "measure", "flag": "measure", "few": "measure"}


def test_bug573_roles_survive_serialization_and_a_restart(tmp_path):
    path = str(tmp_path / "state.db")
    det, rnd, nxt = _detector(store=SqliteStateStore(path))
    st = SourceState.from_json(det._store.peek("s").to_json())
    assert st.baseline["order_id"].role == "identifier"
    assert st.baseline["ref"].value_format == ["A-9"]
    restarted = DriftDetector(state_store=SqliteStateStore(path))
    assert restarted.baseline_distributions("s")["created_on"].role == "date"
    r = restarted.detect(_payload(_rows(300, nxt, rnd), "after-restart"), record_state=False)
    assert not r.drift_detected, r.details


def test_bug573_a_distributions_only_rebaseline_keeps_the_roles():
    det, rnd, nxt = _detector()
    batch = _payload(_rows(500, nxt, rnd), "next")
    det.register_baseline("s", det._compute_distributions(batch))
    assert det.baseline_distributions("s")["order_id"].role == "identifier"
    r = det.detect(_payload(_rows(300, nxt + 500, rnd), "later"), record_state=False)
    assert not r.drift_detected, r.details


@pytest.mark.parametrize("size", [60, 300, 3000])
def test_bug573_clean_batches_of_ids_and_dates_raise_nothing(size):
    """Each batch judged as /uasr/heal judges it (no recorded state). The 24-value
    name column is left out: its own small-sample noise is not what this measures."""
    schema = {k: v for k, v in SCHEMA.items() if k != "name"}
    for seed in range(10):
        det, rnd, nxt = _detector(seed=seed, with_name=False)
        for snapshot in (None, schema):
            rows = _rows(size, nxt, rnd, with_name=False)
            nxt += size
            r = det.detect(_payload(rows, f"{size}-{seed}", snapshot=snapshot), record_state=False)
            assert not r.drift_detected, (size, seed, r.details)


def _fault(det, nxt, rnd, column, fn):
    rows = _rows(300, nxt, rnd)
    for row in rows:
        row[column] = fn(row[column])
    return det.detect(_payload(rows, f"fault-{column}"), record_state=False)


@pytest.mark.parametrize("column,fn,kind", [
    ("order_id", str, "type_change"),                        # retype int -> text
    ("order_id", float, "type_change"),                      # 123 arriving as 123.0
    ("customer_key", lambda v: v * 1000, "format_change"),   # a unit bug on a keyed id
    ("ref", lambda v: v.split("-")[1], "format_change"),     # ORD-0001234 -> 0001234
    ("created_on", lambda v: dt.date.fromisoformat(v).strftime("%m/%d/%Y"), "format_change"),
    ("created_on", lambda v: v.replace("-", ""), "format_change"),
])
def test_bug573_faults_in_identifier_and_date_columns_are_still_caught(column, fn, kind):
    det, rnd, nxt = _detector()
    r = _fault(det, nxt, rnd, column, fn)
    assert r.drift_type == DriftType.SCHEMA and r.drift_vector["type"] == kind, r.details
    assert column in r.affected_columns


def test_bug573_a_renamed_identifier_is_still_schema_drift():
    det, rnd, nxt = _detector()
    rows = [{("order_no" if k == "order_id" else k): v for k, v in r.items()} for r in _rows(300, nxt, rnd)]
    r = det.detect(_payload(rows, "rename"), record_state=False)
    assert r.drift_vector["type"] == "schema_change" and set(r.affected_columns) == {"order_id", "order_no"}


def test_bug573_a_null_identifier_and_a_repeated_identifier_are_exact_alerts():
    det, rnd, nxt = _detector()
    rows = _rows(1000, nxt, rnd)
    rows[17]["order_id"] = None
    r = det.detect(_payload(rows, "null-id"), record_state=False)
    assert r.drift_types == [DriftType.NULL_RATE] and r.affected_columns == ["order_id"], r.details

    rows = _rows(1000, nxt, rnd)
    rows[40]["ref"] = rows[39]["ref"]
    r = det.detect(_payload(rows, "repeated-id"), record_state=False)
    assert r.drift_types == [DriftType.DUPLICATE], r.details
    assert r.drift_vector["repeated_identifiers"] == {"ref": 1}


def test_bug573_measures_beside_identifiers_still_drift():
    det, rnd, nxt = _detector()
    r = _fault(det, nxt, rnd, "amount", lambda v: v * 100)
    assert r.drift_type == DriftType.STATISTICAL and r.affected_columns == ["amount"], r.details


# ── BUG-574: duplicates ────────────────────────────────────────────────────────


def test_bug574_one_exact_copy_is_an_alert_when_the_baseline_has_none():
    det, rnd, nxt = _detector()
    for n, copies in ((300, 1), (300, 15), (40, 2), (3000, 1)):
        rows = _rows(n, nxt, rnd)
        rows += [dict(rows[i]) for i in rnd.sample(range(n), copies)]
        r = det.detect(_payload(rows, f"dup-{n}-{copies}"), record_state=False)
        assert DriftType.DUPLICATE in r.drift_types, (n, copies, r.details)
        assert r.drift_vectors["duplicate"]["duplicate_rows"] == copies


def test_bug574_a_declared_key_is_what_duplicates_are_judged_on():
    det, rnd, nxt = _detector(metadata={"key_columns": ["ref"]})
    assert det._store.peek("s").baseline_rows["key_columns"] == ["ref"]
    rows = _rows(300, nxt, rnd)
    rows.append({**rows[5], "amount": rows[5]["amount"] + 1.0})  # same key, other values
    r = det.detect(_payload(rows, "key-dup"), record_state=False)
    assert DriftType.DUPLICATE in r.drift_types
    assert r.drift_vectors["duplicate"]["duplicate_rows"] == 1


def test_bug574_a_measure_distinct_by_chance_does_not_make_rows_unique():
    """A coarse price that happened not to repeat across 100 baseline rows repeats
    at 2000 rows, and so do whole rows: no identifier, so no exact zero floor."""
    rnd = random.Random(5741)
    while True:
        base = [{"price": round(rnd.uniform(0, 100), 2), "paid": rnd.random() < 0.5} for _ in range(100)]
        if len({r["price"] for r in base}) == len(base):
            break
    det = DriftDetector()
    det.register_baseline("s", _payload(base, "base"))
    assert det._store.peek("s").baseline_rows["unique_columns"] == []
    rows = [{"price": round(rnd.uniform(0, 100), 2), "paid": rnd.random() < 0.5} for _ in range(2000)]
    assert len({(r["price"], r["paid"]) for r in rows}) < len(rows)  # chance duplicates exist
    r = det.detect(_payload(rows, "big"), record_state=False)
    assert DriftType.DUPLICATE not in r.drift_types, r.details


def _categorical_rows(rnd: random.Random, spec, n):
    return [{f"c{j}": rnd.choices(range(len(w)), weights=w)[0] for j, w in enumerate(spec)}
            for _ in range(n)]


def test_bug574_a_baseline_with_duplicates_gives_no_false_alarm_in_1000_clean_trials():
    """Rows from a finite space repeat naturally, more the more rows are drawn: the
    test must hold at any batch size from a tenth to ten times the baseline's, on
    uniform and skewed rows, from two distinct rows to tens of thousands."""
    rnd = random.Random(574)
    alarms, with_dups = [], 0
    for trial in range(1000):
        spec = [[rnd.uniform(0.05, 3.0) ** rnd.choice([1, 3]) for _ in range(rnd.randint(2, 12))]
                for _ in range(rnd.randint(1, 4))]
        n0 = rnd.randint(100, 1200)
        n1 = min(4000, max(2, int(n0 * rnd.choice([0.1, 0.5, 1, 3, 10]))))
        det = DriftDetector()
        det.register_baseline("s", _payload(_categorical_rows(rnd, spec, n0), "base"))
        with_dups += det._store.peek("s").baseline_rows["equal_pairs"] > 0
        r = det.detect(_payload(_categorical_rows(rnd, spec, n1), f"t{trial}"), record_state=False)
        if DriftType.DUPLICATE in r.drift_types:
            alarms.append((trial, n0, n1, r.drift_vectors["duplicate"]["details"]))
    assert with_dups >= 990
    assert alarms == [], f"{len(alarms)} false alarms, e.g. {alarms[:3]}"


def test_bug574_a_baseline_with_few_duplicates_still_catches_injected_copies():
    rnd = random.Random(5740)
    spec = [[1.0] * 12, [1.0] * 10, [1.0] * 8, [1.0] * 20]  # 19 200 equally likely rows
    caught = 0
    for trial in range(20):
        det = DriftDetector()
        det.register_baseline("s", _payload(_categorical_rows(rnd, spec, 600), "base"))
        assert det._store.peek("s").baseline_rows["equal_pairs"] > 0
        rows = _categorical_rows(rnd, spec, 600)
        rows += [dict(rows[i]) for i in rnd.sample(range(600), 180)]
        caught += DriftType.DUPLICATE in det.detect(_payload(rows, f"inj{trial}"), record_state=False).drift_types
    assert caught == 20


# ── End to end through /uasr/heal (the accuracy benchmark's harness) ──────────


@pytest.fixture(scope="module")
def _isolated_metadata_db():
    original = (_metadata_db.DATABASE_URL, _metadata_db._engine, _metadata_db._session_factory)
    tmp_path = os.path.join(tempfile.gettempdir(), f"aura_heal573_{uuid.uuid4().hex[:8]}.db")
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
async def _heal_db(_isolated_metadata_db, monkeypatch):
    await init_uasr_db()
    # No LLM in CI: the proposal path for an unproven fault would reach the network.
    monkeypatch.setattr(SynthesisActuatorAgent, "_llm_shim", _no_llm)
    monkeypatch.setattr(DiagnosticReflectorAgent, "_llm_diagnosis", _no_llm)
    yield


# Seeds the b038757 benchmark reported: clean batches that alarmed on a sequential
# id (28, 41), a text id (8, 27) or a date (5, 95), and duplicate injections that
# came back healed with no alert (165, 22).
@pytest.mark.parametrize("seed", [28, 41, 8, 27, 5, 95, 0, 2])
async def test_bug573_benchmark_clean_batches_come_back_healed(_heal_db, seed):
    trial = build_clean(seed)
    result = classify_clean(trial, await heal(trial))
    assert result.outcome == CORRECT, result.why


@pytest.mark.parametrize("seed", [165, 22])
async def test_bug574_heal_reports_the_duplicate_alert_and_leaves_rows_untouched(_heal_db, seed):
    trial = build_trial("duplicates", seed)
    out = await heal(trial)
    result = classify(trial, out)
    assert result.outcome == CORRECT, result.why
    assert out["healed"] is False and out["degraded"] is True
    assert [a["type"] for a in out["alerts"]] == ["duplicate"]
    assert len(out["rows"]) == len(trial.sent) and not out["quarantined_rows"]
