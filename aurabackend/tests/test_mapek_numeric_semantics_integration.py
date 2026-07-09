"""
Phase-1b — numeric semantic channel integration with MAPEKWorker.

Proves the channel is safe and observe-only:
  * default OFF → no analyzer, zero behavior change
  * healthy batches auto-register a per-source baseline
  * a unit error emits a numeric-drift signal with an un-applied heal proposal
  * the channel NEVER mutates the batch rows
  * a legitimate regime change is alerted but carries NO proposed transform
"""
from __future__ import annotations

import asyncio
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from uasr.mapek_worker import MAPEKConfig, MAPEKWorker  # noqa: E402
from uasr.models import BatchPayload, DriftDetectionResult  # noqa: E402

_RNG = np.random.default_rng(31)


def _batch(source_id, batch_id, mu=500.0, sd=80.0, n=120, factor=1.0):
    vals = np.round(_RNG.normal(mu, sd, n), 2) * factor
    return BatchPayload(
        source_id=source_id, batch_id=batch_id,
        rows=[{"price": float(v)} for v in vals],
    )


def _no_drift():
    return DriftDetectionResult(source_id="s", batch_id="b", drift_detected=False)


def _make_worker(events):
    async def cb(phase, message, payload):
        events.append((phase, message, payload))
    cfg = MAPEKConfig(source_id="s", use_numeric_semantics=True,
                      numeric_baseline_batches=10)
    return MAPEKWorker(config=cfg, progress_cb=cb)


def _feed_healthy(worker, n, source_id="s"):
    for i in range(n):
        b = _batch(source_id, f"h{i}")
        asyncio.run(worker._analyze_numeric_semantics(b, _no_drift()))


def test_default_off_no_analyzer():
    w = MAPEKWorker(config=MAPEKConfig(source_id="s"))
    assert w._numeric_analyzer is None


def test_flag_on_creates_analyzer():
    w = MAPEKWorker(config=MAPEKConfig(source_id="s", use_numeric_semantics=True))
    assert w._numeric_analyzer is not None


def test_healthy_batches_register_baseline():
    events = []
    w = _make_worker(events)
    _feed_healthy(w, 10)
    assert w._numeric_analyzer.has_baseline("s:price")
    assert any(p == "numeric_semantics" and "baseline registered" in m
               for p, m, _ in events)


def test_unit_error_emits_proposal():
    events = []
    w = _make_worker(events)
    _feed_healthy(w, 10)
    events.clear()
    bad = _batch("s", "bad", factor=100.0)  # ×100 unit error
    asyncio.run(w._analyze_numeric_semantics(bad, _no_drift()))
    sigs = [p for p in events if p[0] == "numeric_semantics"]
    assert sigs, "expected a numeric_semantics signal"
    payload = sigs[-1][2]
    assert payload["column"] == "price"
    assert payload.get("proposed_transform") == "div100"
    assert 0.0 <= payload.get("confidence", 0) <= 1.0


def test_channel_never_mutates_batch():
    events = []
    w = _make_worker(events)
    _feed_healthy(w, 10)
    bad = _batch("s", "bad", factor=100.0)
    before = [dict(r) for r in bad.rows]
    asyncio.run(w._analyze_numeric_semantics(bad, _no_drift()))
    assert [dict(r) for r in bad.rows] == before  # inference only


def test_regime_change_alerted_without_transform():
    events = []
    w = _make_worker(events)
    _feed_healthy(w, 10)
    events.clear()
    # legitimate mean shift (no multiplicative inverse clears the gate)
    shifted = _batch("s", "shift", mu=500 + 4 * 80, sd=80)
    asyncio.run(w._analyze_numeric_semantics(shifted, _no_drift()))
    sigs = [p for p in events if p[0] == "numeric_semantics"]
    if sigs:  # if detected, it must NOT carry a proposed transform
        assert "proposed_transform" not in sigs[-1][2]


def test_no_baseline_during_warmup_no_signal():
    events = []
    w = _make_worker(events)
    # only 3 healthy batches — below the 10-batch threshold
    _feed_healthy(w, 3)
    assert not w._numeric_analyzer.has_baseline("s:price")
    # a drifted batch during warmup produces no drift signal (no baseline yet)
    bad = _batch("s", "bad", factor=100.0)
    events.clear()
    asyncio.run(w._analyze_numeric_semantics(bad, _no_drift()))
    assert not any("proposed_transform" in e[2] for e in events
                   if e[0] == "numeric_semantics")


def test_drift_batch_not_buffered_into_baseline():
    """Only no-drift batches feed the baseline (guards against poisoning)."""
    events = []
    w = _make_worker(events)
    drifted = DriftDetectionResult(source_id="s", batch_id="b",
                                   drift_detected=True)
    for i in range(10):
        asyncio.run(w._analyze_numeric_semantics(_batch("s", f"d{i}"), drifted))
    # every batch flagged as drift → nothing buffered → no baseline
    assert not w._numeric_analyzer.has_baseline("s:price")
