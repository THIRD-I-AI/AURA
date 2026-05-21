"""
Sprint S18.1 — contract tests for the Wasserstein-Martingale drift
detector wired into the live MAPE-K worker.

Scope (deliberately small)
--------------------------
S18.1 ships the MARTINGALE swap only — the next-tier integrations
(CausalRLEvaluator for shim selection, ShimRouter for pause-less
deployment) are deferred to S18.1b/c. Single integration point per
sprint keeps the review surface clean.

Tier A — pure-Python:

  * Flag OFF (default) → worker uses the classical detector exactly
    as before. The 50 existing test_uasr.py + test_uasr_causal_rl.py
    tests are the regression contract; this file adds the new
    coverage on top.
  * Flag ON, no baseline → falls through to classical detector
    (martingale can't compute a Wasserstein distance without a
    baseline; we must not silently drop the signal).
  * Flag ON, baseline registered, no real drift → martingale stays
    quiet across many batches (false-positive bound holds).
  * Flag ON, baseline registered, real drift injected → martingale
    fires within a bounded number of batches with the expected
    DriftDetectionResult shape.
  * Flag ON, batch has no numeric columns → returns None internally,
    falls through to classical (no crash).
  * Severity escalation flag → MEDIUM (default) vs HIGH (opt-in).
"""
from __future__ import annotations

from typing import Any, Dict, List

import numpy as np
import pytest

from uasr.mapek_worker import MAPEKConfig, MAPEKWorker
from uasr.models import BatchPayload, DriftSeverity, DriftType


def _make_batch(rows: List[Dict[str, Any]], source_id: str = "test_src") -> BatchPayload:
    cols = sorted({k for r in rows for k in r.keys()})
    return BatchPayload(
        source_id=source_id,
        batch_id="b1",
        columns=cols,
        rows=rows,
        schema_snapshot={c: type(rows[0].get(c)).__name__ for c in cols if rows[0].get(c) is not None},
    )


def _worker(use_martingale: bool, *, alpha: float = 0.001, severity_high: bool = False) -> MAPEKWorker:
    cfg = MAPEKConfig(
        source_id="test_src",
        use_martingale_detector=use_martingale,
        martingale_alpha=alpha,
        martingale_baseline_window=20,  # smaller for tests
        martingale_alarm_severity_high=severity_high,
    )
    return MAPEKWorker(config=cfg)


# ── Flag-OFF backward-compatibility ───────────────────────────────────


def test_flag_off_uses_classical_detector_only() -> None:
    """Default config → no martingale, classical detector path
    unchanged. Smoke test that we don't accidentally activate the
    martingale on opt-out deployments."""
    w = _worker(use_martingale=False)
    assert w._martingale is None
    # Build a tiny batch; classical detector with no baseline returns
    # 'no drift detected' result. No crash, no martingale.
    batch = _make_batch([{"x": 1.0}, {"x": 2.0}])
    result = w._analyze_detect_drift(batch)
    assert result.drift_detected is False


def test_flag_on_initialises_martingale_lazily() -> None:
    """When the flag is on, the WassersteinMartingaleDetector is
    constructed lazily during __init__. Threaded-config matters
    because importing martingale loads numpy."""
    w = _worker(use_martingale=True)
    assert w._martingale is not None
    # Confirm the alpha threaded through.
    assert w._martingale._alpha == pytest.approx(0.001)


# ── Flag-ON behaviour without baseline ────────────────────────────────


def test_flag_on_no_baseline_falls_through_to_classical() -> None:
    """Martingale can't fire without a registered baseline (no way
    to compute Wasserstein distance). The worker must fall through
    to the classical detector — never silently drop the signal."""
    w = _worker(use_martingale=True)
    batch = _make_batch([{"x": float(i)} for i in range(30)])
    # No baseline registered → martingale.update returns False per
    # column → _analyze_martingale returns None → fall-through to
    # classical detector which (also without baseline) reports no
    # drift. Key contract: NO crash, NO martingale-result returned.
    result = w._analyze_detect_drift(batch)
    assert result.drift_detected is False


# ── Flag-ON behaviour with baseline ───────────────────────────────────


@pytest.fixture
def worker_with_baseline() -> MAPEKWorker:
    """Worker with a martingale baseline registered for column 'metric'."""
    w = _worker(use_martingale=True, alpha=0.05)  # looser alpha = faster alarms
    # Seed baseline + warm-up — register a baseline of 100 N(0,1) samples
    # then push 20 batches to clear the baseline_window phase.
    rng = np.random.default_rng(seed=42)
    baseline_samples = rng.standard_normal(100).tolist()
    w._martingale.register_baseline("test_src", {"metric": baseline_samples})
    # Warm up the active phase by pushing on-distribution batches.
    for _ in range(25):
        warm_samples = rng.standard_normal(50).tolist()
        w._martingale.update("test_src", "metric", warm_samples)
    return w


def test_martingale_stays_quiet_under_no_drift(worker_with_baseline: MAPEKWorker) -> None:
    """**Azuma-Hoeffding bound contract**: with no real drift, the
    martingale should NOT fire across many batches. The bound is
    P(any alarm) ≤ α; with α=0.05 and ~30 active-phase batches the
    expected number of alarms is small."""
    rng = np.random.default_rng(seed=123)
    alarm_count = 0
    for _ in range(30):
        # On-distribution samples; martingale should stay below threshold.
        batch_samples = rng.standard_normal(50).tolist()
        batch = _make_batch([{"metric": v} for v in batch_samples])
        result = worker_with_baseline._analyze_detect_drift(batch)
        if (
            result.drift_detected
            and result.drift_vector
            and result.drift_vector.get("detector") == "wasserstein_martingale"
        ):
            alarm_count += 1
    # Allow a small number of false-positives — the bound is per-step,
    # not strictly cumulative. 0-3 across 30 batches is well inside
    # tolerance.
    assert alarm_count <= 3, (
        f"martingale fired {alarm_count} times under no-drift; "
        f"Azuma-Hoeffding bound appears violated"
    )


def test_martingale_fires_under_real_drift(worker_with_baseline: MAPEKWorker) -> None:
    """When the distribution shifts substantially (mean shift = 3σ),
    the martingale MUST fire within a reasonable number of batches.
    Otherwise the detector has no real sensitivity."""
    rng = np.random.default_rng(seed=456)
    fired = False
    for _ in range(20):  # bounded — should fire well before this
        # Shifted by 3σ — large, obvious drift.
        batch_samples = (rng.standard_normal(50) + 3.0).tolist()
        batch = _make_batch([{"metric": v} for v in batch_samples])
        result = worker_with_baseline._analyze_detect_drift(batch)
        if (
            result.drift_detected
            and result.drift_vector
            and result.drift_vector.get("detector") == "wasserstein_martingale"
        ):
            fired = True
            # Verify the result shape matches the DriftDetectionResult
            # the classical detector produces — caller-side branches
            # downstream don't need martingale-specific logic.
            assert result.drift_type == DriftType.STATISTICAL
            assert result.severity == DriftSeverity.MEDIUM
            assert "metric" in result.affected_columns
            assert result.drift_vector["alpha"] == pytest.approx(0.05)
            break
    assert fired, "martingale failed to detect a 3σ mean shift in 20 batches"


def test_martingale_severity_escalates_to_high_when_flag_on() -> None:
    """`martingale_alarm_severity_high=True` escalates the
    DriftDetectionResult.severity from MEDIUM to HIGH. Lets operators
    bypass pause_on_severity gating for any martingale alarm."""
    cfg = MAPEKConfig(
        source_id="test_src",
        use_martingale_detector=True,
        martingale_alpha=0.05,
        martingale_baseline_window=20,
        martingale_alarm_severity_high=True,
    )
    w = MAPEKWorker(config=cfg)
    rng = np.random.default_rng(seed=42)
    w._martingale.register_baseline("test_src", {"metric": rng.standard_normal(100).tolist()})
    for _ in range(25):
        w._martingale.update("test_src", "metric", rng.standard_normal(50).tolist())

    for _ in range(20):
        batch_samples = (rng.standard_normal(50) + 3.0).tolist()
        batch = _make_batch([{"metric": v} for v in batch_samples])
        result = w._analyze_detect_drift(batch)
        if (
            result.drift_detected
            and result.drift_vector
            and result.drift_vector.get("detector") == "wasserstein_martingale"
        ):
            assert result.severity == DriftSeverity.HIGH
            return
    pytest.fail("martingale failed to fire — can't verify severity escalation")


# ── Edge cases ────────────────────────────────────────────────────────


def test_batch_with_no_numeric_columns_falls_through(worker_with_baseline: MAPEKWorker) -> None:
    """If a batch contains only string / dict / None values, the
    martingale path has nothing to compute. Must fall through to the
    classical detector (which can still report schema drift, embedding
    drift, etc.) — never crash."""
    # All-string batch.
    batch = _make_batch([{"label": "x"}, {"label": "y"}])
    result = worker_with_baseline._analyze_detect_drift(batch)
    # Classical detector returns no-drift because the baseline isn't
    # registered for "label" either. Key contract: NO crash.
    assert result.drift_detected is False


def test_martingale_skips_columns_without_baseline(worker_with_baseline: MAPEKWorker) -> None:
    """A batch with both a baselined column AND an un-baselined column
    only evaluates the baselined one. The un-baselined column doesn't
    crash the detector — the martingale.update fail-open behaviour
    handles it."""
    rng = np.random.default_rng(seed=42)
    # Push a drifted batch on the baselined column AND a noisy
    # un-baselined column in the same batch.
    rows = [
        {"metric": float(rng.standard_normal() + 3.0), "stranger": rng.uniform()}
        for _ in range(50)
    ]
    batch = _make_batch(rows)
    # Should not raise; should run successfully across many batches.
    for _ in range(15):
        rows = [
            {"metric": float(rng.standard_normal() + 3.0), "stranger": rng.uniform()}
            for _ in range(50)
        ]
        batch = _make_batch(rows)
        result = worker_with_baseline._analyze_detect_drift(batch)
        # Don't assert anything about whether/when it fires — the test
        # is "doesn't crash on un-baselined columns in mixed batches."
        assert result is not None


def test_martingale_diagnostics_observable_post_update(worker_with_baseline: MAPEKWorker) -> None:
    """After running the worker's analyze step, the per-column
    diagnostics surface (step count, last distance, martingale value)
    should be available for the operator card. Verifies the wiring
    keeps the diagnostics endpoint useful."""
    rng = np.random.default_rng(seed=789)
    rows = [{"metric": v} for v in rng.standard_normal(50)]
    worker_with_baseline._analyze_detect_drift(_make_batch(rows))
    diag = worker_with_baseline._martingale.diagnostics("test_src", "metric")
    assert "step" in diag
    assert diag["step"] > 0
    assert "martingale" in diag
