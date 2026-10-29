"""ADR-002 phase 1: drift-detector accuracy (BUG-564, BUG-568).

Tier A (pure Python + numpy). Every check runs against the real DriftDetector
with the default zeta and warmup_batches=0 -- no warmup suppression and no
adaptive threshold to hide a false alarm -- and record_state=False, so each
trial is judged against the flat threshold, the harshest setting.

The clean set: the same generator as the baseline, a fresh seed per trial, at
0.1x, 1x and 10x the baseline's 2000 rows, 200 trials. ADR-002 requires zero
false alarms on it.
"""
from __future__ import annotations

import numpy as np
import pytest

from uasr.drift_detector import DriftDetector
from uasr.models import BatchPayload, ColumnDistribution, DriftType
from uasr.state_store import SourceState

BASELINE_ROWS = 2000
TRIALS = 200
SIZES = (0.1, 1, 10)
COLUMNS = ["x", "y", "cat"]


def _rows(rng, n, shift=0.0, spread=1.0, null_x=0.1):
    """x ~ N(100, 15) with 10% nulls, y lognormal, cat a skewed 5-way category."""
    x = 100.0 + 15.0 * (rng.standard_normal(n) * spread + shift)
    y = rng.lognormal(3.0, 0.5, n)
    cat = rng.choice(["a", "b", "c", "d", "e"], size=n, p=[0.5, 0.2, 0.15, 0.1, 0.05])
    nulls = rng.random(n) < null_x
    return [
        {"x": None if nulls[i] else float(x[i]), "y": float(y[i]), "cat": str(cat[i])}
        for i in range(n)
    ]


def _batch(rows, bid="b", columns=COLUMNS):
    return BatchPayload(source_id="s", batch_id=bid, columns=list(columns), rows=rows)


def _detector(baseline_rows):
    det = DriftDetector(warmup_batches=0)
    det.register_baseline("s", _batch(baseline_rows, "base"))
    return det


def _trial_batches(gen=_rows, **kw):
    """(trial, size, detector, batch) over the whole trial set."""
    for t in range(TRIALS):
        rng = np.random.default_rng(10_000 + t)
        det = _detector(gen(rng, BASELINE_ROWS))
        for size in SIZES:
            yield t, size, det, _batch(gen(rng, int(BASELINE_ROWS * size), **kw), f"{t}-{size}")


# ── BUG-564: bins aligned to the baseline ──────────────────────────


def test_bug564_clean_batches_of_any_size_report_no_statistical_drift():
    hits = []
    for t, size, det, batch in _trial_batches():
        r = det.detect(batch, record_state=False)
        if DriftType.STATISTICAL in r.drift_types or r.drift_type == DriftType.STATISTICAL:
            hits.append((t, size, r.drift_vector.get("kl_values")))
    assert hits == [], f"{len(hits)} statistical false alarms on clean data, e.g. {hits[:3]}"


@pytest.mark.parametrize("shift,spread", [(1.0, 1.0), (0.0, 10.0)], ids=["mean+1sd", "scale-x10"])
def test_bug564_real_shift_is_detected_in_every_trial(shift, spread):
    misses = []
    for t, size, det, batch in _trial_batches(shift=shift, spread=spread):
        r = det.detect(batch, record_state=False)
        if not (r.drift_type == DriftType.STATISTICAL and "x" in r.affected_columns):
            misses.append((t, size, r.drift_type, r.drift_vector.get("kl_values")))
    assert misses == [], f"{len(misses)} missed shifts, e.g. {misses[:3]}"


def test_bug564_baseline_rebins_onto_its_own_edges_exactly():
    rng = np.random.default_rng(1)
    values = rng.normal(0.0, 1.0, 5000)
    hist = DriftDetector._build_numeric_histogram(values)
    edges = DriftDetector._baseline_edges(hist)
    rebinned = DriftDetector._histogram_on_edges(values, edges)
    assert rebinned["counts"] == [0, *hist["counts"], 0]
    kl, significant = DriftDetector._aligned_kl(rebinned["counts"], hist["counts"])
    assert kl < 0.01 and significant is False


def test_bug564_values_past_the_baseline_range_land_in_the_end_bins():
    edges = DriftDetector._baseline_edges(DriftDetector._build_numeric_histogram([0.0, 10.0]))
    counts = DriftDetector._histogram_on_edges([-5.0, 0.0, 5.0, 10.0, 99.0], edges)["counts"]
    assert counts[0] == 1 and counts[-1] == 1 and sum(counts[1:-1]) == 3
    # A constant baseline gets below / equal / above bins.
    const = DriftDetector._baseline_edges(DriftDetector._build_numeric_histogram([3.0, 3.0]))
    assert DriftDetector._histogram_on_edges([1.0, 3.0, 3.0, 4.0], const)["counts"] == [1, 2, 1]


def test_bug564_caller_supplied_histograms_keep_the_index_wise_comparison():
    # bins/counts of equal length are not edges: no alignment is attempted.
    assert DriftDetector._baseline_edges({"bins": [1, 2, 3], "counts": [10, 20, 30]}) is None


# ── BUG-568: null rate, volume, duplicates; every drift type reported ──


def test_bug568_clean_set_raises_no_drift_of_any_type():
    hits = []
    for t, size, det, batch in _trial_batches():
        r = det.detect(batch, record_state=False)
        if r.drift_detected or r.drift_types:
            hits.append((t, size, [d.value for d in r.drift_types]))
    assert hits == [], f"{len(hits)} false alarms on clean data, e.g. {hits[:3]}"


def _low_cardinality(rng, n):
    a = rng.choice([1, 2, 3], size=n, p=[0.8, 0.15, 0.05])
    b = rng.choice(["u", "v"], size=n, p=[0.9, 0.1])
    return [{"a": int(a[i]), "b": str(b[i])} for i in range(n)]


def test_bug568_natural_repeats_at_10x_are_not_duplicate_drift():
    # Six possible rows: a 10x batch repeats far more often than its
    # baseline, purely because it is bigger. Comparing duplicate RATES would
    # flag every such batch; the size-scaled pair count must not.
    for t in range(TRIALS):
        rng = np.random.default_rng(20_000 + t)
        base = _low_cardinality(rng, BASELINE_ROWS)
        det = DriftDetector(warmup_batches=0)
        det.register_baseline("s", _batch(base, "base", ["a", "b"]))
        for size in SIZES:
            batch = _batch(_low_cardinality(rng, int(BASELINE_ROWS * size)), f"{t}", ["a", "b"])
            assert not det.detect(batch, record_state=False).drift_types, (t, size)


def test_bug568_blanked_column_is_null_rate_drift():
    rng = np.random.default_rng(3)
    det = _detector(_rows(rng, BASELINE_ROWS))
    for size in SIZES:
        rows = _rows(rng, int(BASELINE_ROWS * size))
        for r in rows:
            r["y"] = None
        res = det.detect(_batch(rows), record_state=False)
        assert res.drift_detected and DriftType.NULL_RATE in res.drift_types, size
        vec = res.drift_vectors["null_rate"]
        assert vec["affected_columns"] == ["y"]
        assert vec["null_rates"]["y"] == {"baseline_null_rate": 0.0, "batch_null_rate": 1.0}


def test_bug568_null_spike_on_a_nullable_column_is_detected():
    rng = np.random.default_rng(4)
    det = _detector(_rows(rng, BASELINE_ROWS))  # x is 10% null
    res = det.detect(_batch(_rows(rng, BASELINE_ROWS, null_x=0.4)), record_state=False)
    assert res.drift_type == DriftType.NULL_RATE
    assert res.affected_columns == ["x"]


def test_bug568_a_column_absent_from_the_batch_is_schema_not_null_drift():
    rng = np.random.default_rng(5)
    det = _detector(_rows(rng, BASELINE_ROWS))
    rows = [{k: v for k, v in r.items() if k != "y"} for r in _rows(rng, BASELINE_ROWS)]
    res = det.detect(_batch(rows, columns=["x", "cat"]), record_state=False)
    assert res.drift_type == DriftType.SCHEMA
    assert DriftType.NULL_RATE not in res.drift_types


def test_bug568_volume_drop_and_spike():
    rng = np.random.default_rng(6)
    det = _detector(_rows(rng, BASELINE_ROWS))
    drop = det.detect(_batch(_rows(rng, 12)), record_state=False)
    assert DriftType.VOLUME in drop.drift_types
    assert drop.drift_vectors["volume"]["baseline_rows"] == BASELINE_ROWS
    empty = det.detect(_batch([]), record_state=False)
    assert empty.drift_type == DriftType.VOLUME and empty.severity.value == "critical"
    # 0.1x and 10x are inside the clean range (covered by the 200-trial set).
    assert DriftType.VOLUME not in det.detect(_batch(_rows(rng, 200)), record_state=False).drift_types


def test_bug568_volume_works_for_a_distributions_only_baseline():
    rng = np.random.default_rng(7)
    det = DriftDetector(warmup_batches=0)
    det.register_baseline("s", det.snapshot_distributions(_batch(_rows(rng, BASELINE_ROWS))))
    assert DriftType.VOLUME in det.detect(_batch(_rows(rng, 5)), record_state=False).drift_types


def test_bug568_replayed_rows_are_duplicate_drift():
    rng = np.random.default_rng(8)
    det = _detector(_rows(rng, BASELINE_ROWS))
    rows = _rows(rng, BASELINE_ROWS)
    rows = rows[:1800] + [dict(rows[i]) for i in range(200)]  # 10% replayed
    res = det.detect(_batch(rows), record_state=False)
    assert res.drift_type == DriftType.DUPLICATE
    assert res.drift_vectors["duplicate"]["duplicate_rows"] == 200


def test_bug568_distributions_only_rebaseline_drops_the_duplicate_profile():
    rng = np.random.default_rng(9)
    det = _detector(_rows(rng, BASELINE_ROWS))
    assert det._store.peek("s").baseline_rows["rows"] == BASELINE_ROWS
    det.register_baseline("s", det.snapshot_distributions(_batch(_rows(rng, BASELINE_ROWS))))
    assert det._store.peek("s").baseline_rows is None


def test_bug568_every_drift_type_is_reported_and_the_first_hit_is_unchanged():
    rng = np.random.default_rng(10)
    det = _detector(_rows(rng, BASELINE_ROWS))
    rows = _rows(rng, BASELINE_ROWS, shift=1.0)
    for r in rows:
        r["y"] = None
        r["extra"] = 1
    res = det.detect(_batch(rows, columns=[*COLUMNS, "extra"]), record_state=False)
    # Old precedence for the first hit: schema before statistical.
    assert res.drift_type == DriftType.SCHEMA
    assert res.drift_vector["type"] == "schema_change"
    assert res.kl_divergence is None
    assert res.drift_types[:2] == [DriftType.SCHEMA, DriftType.STATISTICAL]
    assert DriftType.NULL_RATE in res.drift_types
    assert set(res.drift_vectors) == {t.value for t in res.drift_types}
    assert res.drift_vectors["statistical"]["affected_columns"] == ["x"]


def test_bug568_a_schema_drifted_batch_still_adds_no_kl_sample():
    rng = np.random.default_rng(11)
    det = _detector(_rows(rng, BASELINE_ROWS))
    rows = _rows(rng, BASELINE_ROWS)
    for r in rows:
        r["extra"] = 1
    det.detect(_batch(rows, columns=[*COLUMNS, "extra"]))
    assert det._store.peek("s").kl_history == []
    det.detect(_batch(_rows(rng, BASELINE_ROWS)))
    assert len(det._store.peek("s").kl_history) == 1


def test_bug568_clean_result_has_empty_drift_list():
    rng = np.random.default_rng(12)
    det = _detector(_rows(rng, BASELINE_ROWS))
    res = det.detect(_batch(_rows(rng, BASELINE_ROWS)), record_state=False)
    assert res.drift_detected is False and res.drift_type is None
    assert res.drift_types == [] and res.drift_vectors == {}
    assert res.details == "No drift detected"


def test_bug568_row_profile_survives_state_serialization():
    st = SourceState(
        baseline={"v": ColumnDistribution(column_name="v", sample_size=10, null_rate=0.0)},
        baseline_rows={"rows": 10, "columns": ["v"], "equal_pairs": 3},
    )
    back = SourceState.from_json(st.to_json())
    assert back.baseline_rows == {"rows": 10, "columns": ["v"], "equal_pairs": 3}
    assert SourceState.from_json(SourceState().to_json()).baseline_rows is None
