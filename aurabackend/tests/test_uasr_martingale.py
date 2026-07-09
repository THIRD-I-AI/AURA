"""
Sprint S32 — UASR Wasserstein-Martingale drift detector tests.

Tier A (pure Python, no optional deps).

Covers:
  * wasserstein_1_empirical: identical, shifted, different-length,
    empty, single-element distributions
  * _resample_at_positions: length matching
  * azuma_hoeffding_bound: basic computation, edge cases, input validation
  * WassersteinMartingaleDetector: baseline learning (no alarm),
    stable stream (no alarm), drift injection (alarm fires),
    reset_source, diagnostics, alarm_persistence
"""
from __future__ import annotations

import math
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from uasr.martingale import (
    WassersteinMartingaleDetector,
    _resample_at_positions,
    azuma_hoeffding_bound,
    wasserstein_1_empirical,
)

# ── wasserstein_1_empirical ───────────────────────────────────────

class TestWasserstein1:
    def test_identical_distributions(self):
        a = [1.0, 2.0, 3.0, 4.0, 5.0]
        assert wasserstein_1_empirical(a, a) == 0.0

    def test_shifted_distribution(self):
        a = [0.0, 1.0, 2.0]
        b = [1.0, 2.0, 3.0]
        assert abs(wasserstein_1_empirical(a, b) - 1.0) < 1e-10

    def test_symmetric(self):
        a = [1.0, 3.0, 5.0]
        b = [2.0, 4.0, 6.0]
        assert abs(
            wasserstein_1_empirical(a, b) - wasserstein_1_empirical(b, a)
        ) < 1e-10

    def test_different_lengths_resampled(self):
        a = [0.0, 1.0]
        b = [0.0, 0.5, 1.0]
        dist = wasserstein_1_empirical(a, b)
        assert dist >= 0.0

    def test_both_empty(self):
        assert wasserstein_1_empirical([], []) == 0.0

    def test_one_empty_raises(self):
        with pytest.raises(ValueError, match="undefined"):
            wasserstein_1_empirical([1.0], [])
        with pytest.raises(ValueError, match="undefined"):
            wasserstein_1_empirical([], [1.0])

    def test_single_element(self):
        assert abs(wasserstein_1_empirical([5.0], [8.0]) - 3.0) < 1e-10

    def test_unsorted_input_handled(self):
        a = [5.0, 1.0, 3.0]
        b = [6.0, 2.0, 4.0]
        dist = wasserstein_1_empirical(a, b)
        assert abs(dist - 1.0) < 1e-10


# ── _resample_at_positions ────────────────────────────────────────

class TestResample:
    def test_same_length_is_identity(self):
        vals = [1.0, 2.0, 3.0]
        assert _resample_at_positions(vals, 3) == vals

    def test_upsample(self):
        result = _resample_at_positions([0.0, 10.0], 3)
        assert len(result) == 3
        assert result[0] == 0.0
        assert result[-1] == 10.0
        assert abs(result[1] - 5.0) < 1e-10

    def test_empty_input(self):
        assert _resample_at_positions([], 5) == []

    def test_target_zero(self):
        assert _resample_at_positions([1.0, 2.0], 0) == []


# ── azuma_hoeffding_bound ─────────────────────────────────────────

class TestAzumaHoeffdingBound:
    def test_basic_computation(self):
        bound = azuma_hoeffding_bound(n_steps=100, alpha=0.001, increment_max=1.0)
        expected = math.sqrt(2.0 * math.log(1000.0) * 100 * 1.0)
        assert abs(bound - expected) < 1e-10

    def test_bound_grows_with_steps(self):
        b1 = azuma_hoeffding_bound(10, 0.01, 1.0)
        b2 = azuma_hoeffding_bound(100, 0.01, 1.0)
        assert b2 > b1

    def test_tighter_alpha_gives_larger_bound(self):
        b_loose = azuma_hoeffding_bound(50, 0.1, 1.0)
        b_tight = azuma_hoeffding_bound(50, 0.001, 1.0)
        assert b_tight > b_loose

    def test_invalid_alpha_raises(self):
        with pytest.raises(ValueError):
            azuma_hoeffding_bound(10, 0.0, 1.0)
        with pytest.raises(ValueError):
            azuma_hoeffding_bound(10, 1.0, 1.0)
        with pytest.raises(ValueError):
            azuma_hoeffding_bound(10, -0.1, 1.0)

    def test_invalid_steps_raises(self):
        with pytest.raises(ValueError):
            azuma_hoeffding_bound(0, 0.01, 1.0)

    def test_invalid_increment_raises(self):
        with pytest.raises(ValueError):
            azuma_hoeffding_bound(10, 0.01, 0.0)


# ── WassersteinMartingaleDetector ─────────────────────────────────

class TestDetector:
    def _make_detector(self, baseline_window=10, alpha=0.01):
        return WassersteinMartingaleDetector(
            alpha=alpha, baseline_window=baseline_window,
        )

    def test_invalid_alpha_rejected(self):
        with pytest.raises(ValueError):
            WassersteinMartingaleDetector(alpha=0.0)

    def test_invalid_baseline_window_rejected(self):
        with pytest.raises(ValueError):
            WassersteinMartingaleDetector(baseline_window=5)

    def test_no_alarm_during_baseline_learning(self):
        det = self._make_detector(baseline_window=10)
        det.register_baseline("src", {"col": [0.0, 0.2, 0.4, 0.6, 0.8, 1.0]})
        for _ in range(10):
            alarm = det.update("src", "col", [0.1, 0.3, 0.5, 0.7, 0.9])
            assert alarm is False

    def test_stable_stream_no_alarm(self):
        det = self._make_detector(baseline_window=10)
        baseline = [float(i) / 100 for i in range(100)]
        det.register_baseline("src", {"col": baseline})
        for _ in range(15):
            alarm = det.update("src", "col", baseline)
            assert alarm is False

    def test_drift_injection_triggers_alarm(self):
        det = self._make_detector(baseline_window=10, alpha=0.01)
        baseline = [0.0, 0.1, 0.2, 0.3, 0.4, 0.5]
        det.register_baseline("src", {"col": baseline})
        for _ in range(10):
            det.update("src", "col", baseline)
        drifted = [0.9, 0.95, 0.98, 0.99, 1.0, 1.0]
        alarm_fired = False
        for _ in range(200):
            if det.update("src", "col", drifted):
                alarm_fired = True
                break
        assert alarm_fired

    def test_unregistered_source_returns_false(self):
        det = self._make_detector()
        assert det.update("unknown", "col", [1.0, 2.0]) is False

    def test_unregistered_column_returns_false(self):
        det = self._make_detector()
        det.register_baseline("src", {"col_a": [1.0, 2.0]})
        assert det.update("src", "col_b", [1.0, 2.0]) is False

    def test_reset_source(self):
        det = self._make_detector(baseline_window=10)
        det.register_baseline("src", {"col": [0.0, 1.0]})
        for _ in range(5):
            det.update("src", "col", [0.0, 1.0])
        det.reset_source("src")
        assert det.update("src", "col", [0.0, 1.0]) is False

    def test_diagnostics(self):
        det = self._make_detector(baseline_window=10)
        det.register_baseline("src", {"col": [0.0, 0.5, 1.0]})
        for _ in range(12):
            det.update("src", "col", [0.0, 0.5, 1.0])
        diag = det.diagnostics("src", "col")
        assert diag["step"] == 12.0
        assert diag["threshold"] > 0
        assert diag["e_dw"] >= 0

    def test_expected_distance_none_during_baseline(self):
        det = self._make_detector(baseline_window=10)
        det.register_baseline("src", {"col": [0.0, 1.0]})
        det.update("src", "col", [0.0, 1.0])
        assert det.expected_distance("src", "col") is None

    def test_alarm_persistence(self):
        det = WassersteinMartingaleDetector(
            alpha=0.01, baseline_window=10,
            alarm_persistence=3,
        )
        baseline = [0.0, 0.1, 0.2, 0.3, 0.4, 0.5]
        det.register_baseline("src", {"col": baseline})
        for _ in range(10):
            det.update("src", "col", baseline)
        drifted = [0.9, 0.95, 0.98, 0.99, 1.0, 1.0]
        crossings_before_alarm = 0
        for _ in range(500):
            result = det.update("src", "col", drifted)
            if not result:
                diag = det.diagnostics("src", "col")
                if diag["crossings"] > 0:
                    crossings_before_alarm = max(
                        crossings_before_alarm, int(diag["crossings"])
                    )
            else:
                break
        assert crossings_before_alarm >= 2


# ── Regression: increment_max auto-calibration (fix 3c) ───────────

class TestIncrementAutoCalibration:
    """Fix 3c — a static ``increment_max=1.0`` is 15-30x looser than real
    null W1 increments, making ε(t) so wide the martingale never crosses it
    (silent detector).  Auto-calibration derives the bound per (source,
    column) from baseline-window dispersion.
    """

    def _stream(self, det, shift, n_active=200, baseline_window=30, seed=0):
        import random
        rnd = random.Random(seed)
        baseline = [rnd.gauss(10, 2) for _ in range(200)]
        det.register_baseline("s", {"v": baseline})
        fired_at = None
        for t in range(baseline_window + n_active):
            loc = 10.0 if t < baseline_window else 10.0 + shift
            samples = [rnd.gauss(loc, 2) for _ in range(100)]
            if det.update("s", "v", samples) and fired_at is None:
                fired_at = t - baseline_window
        return fired_at

    def test_calibrated_bound_is_set_and_small(self):
        det = WassersteinMartingaleDetector(
            alpha=0.01, baseline_window=30, auto_calibrate_increment=True
        )
        self._stream(det, shift=0.0, n_active=5, baseline_window=30, seed=1)
        c = det.diagnostics("s", "v")["increment_max"]
        # Calibrated well below the silent default of 1.0
        assert 0.0 < c < 0.6

    def test_default_bound_is_silent_under_drift(self):
        det = WassersteinMartingaleDetector(
            alpha=0.01, baseline_window=30, increment_max=1.0,
            auto_calibrate_increment=False,
        )
        fired = self._stream(det, shift=0.30, baseline_window=30, seed=2)
        assert fired is None  # static 1.0 bound → no alarm even under real drift

    def test_calibrated_detects_drift(self):
        det = WassersteinMartingaleDetector(
            alpha=0.05, baseline_window=30, auto_calibrate_increment=True
        )
        fired = self._stream(det, shift=0.50, baseline_window=30, seed=3)
        assert fired is not None  # auto-calibrated bound recovers detection

    def test_calibrated_null_no_alarm(self):
        det = WassersteinMartingaleDetector(
            alpha=0.001, baseline_window=30, auto_calibrate_increment=True
        )
        fired = self._stream(det, shift=0.0, baseline_window=30, seed=4)
        assert fired is None  # tight α → no false alarm on a null stream

    def test_reset_clears_calibration(self):
        det = WassersteinMartingaleDetector(
            alpha=0.01, baseline_window=30, auto_calibrate_increment=True
        )
        self._stream(det, shift=0.0, n_active=5, baseline_window=30, seed=5)
        det.reset_source("s")
        assert "s" not in det._increment_max_cal

    def test_invalid_calibration_params_rejected(self):
        with pytest.raises(ValueError):
            WassersteinMartingaleDetector(increment_calibration_quantile=0.0)
        with pytest.raises(ValueError):
            WassersteinMartingaleDetector(increment_calibration_quantile=1.5)
        with pytest.raises(ValueError):
            WassersteinMartingaleDetector(increment_calibration_scale=0.0)
