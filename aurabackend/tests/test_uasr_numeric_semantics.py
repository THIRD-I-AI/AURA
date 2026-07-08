"""
UASR NumericSemanticAnalyzer tests (Phase 1 — inference only).

Covers:
  * encode_column: shape, empty/constant edge cases, scale sensitivity
  * NumericBaseline: fit/serialize, per-source drift on unit errors
  * NumericSemanticAnalyzer: two-tier routing, heal PROPOSAL (never mutates),
    the safety property (legitimate regime change is detected but NOT healed)
  * numeric_columns_from_rows: all-or-nothing numeric typing
"""
from __future__ import annotations

import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from uasr.numeric_semantics import (  # noqa: E402
    NumericBaseline,
    NumericSemanticAnalyzer,
    TypePrototypes,
    encode_column,
    numeric_columns_from_rows,
)

_RNG = np.random.default_rng(20260707)


def _healthy(n=120, mu=500.0, sd=80.0):
    return np.round(_RNG.normal(mu, sd, n), 2)


def _batches(m=15, **kw):
    return [_healthy(**kw) for _ in range(m)]


# ── encoder ───────────────────────────────────────────────────────────────

class TestEncodeColumn:
    def test_shape_is_16(self):
        assert encode_column(_healthy()).shape == (16,)

    def test_empty_is_zeros(self):
        assert np.array_equal(encode_column([]), np.zeros(16))

    def test_constant_column_is_finite(self):
        f = encode_column([7.0] * 50)
        assert np.all(np.isfinite(f))

    def test_scale_shift_moves_magnitude_feature(self):
        base = encode_column(_healthy(mu=500))
        scaled = encode_column(_healthy(mu=500) * 100)
        # dim 0 is log-magnitude: ×100 adds ~2 decades
        assert scaled[0] - base[0] > 1.5


# ── per-source baseline ─────────────────────────────────────────────────────

class TestNumericBaseline:
    def test_fit_and_serialize(self):
        b = NumericBaseline.fit(_batches())
        assert len(b.mu) == 16 and len(b.sigma) == 16
        assert b.n_batches == 15
        assert all(isinstance(x, float) for x in b.mu)  # JSON-friendly

    def test_healthy_batch_not_drift(self):
        b = NumericBaseline.fit(_batches())
        assert b.is_drift(_healthy()) is False

    def test_unit_error_is_drift(self):
        b = NumericBaseline.fit(_batches())
        assert b.is_drift(_healthy() * 100) is True


# ── analyzer routing + proposals ────────────────────────────────────────────

class TestAnalyzer:
    def test_no_baseline_no_prototypes_reports_no_drift(self):
        a = NumericSemanticAnalyzer()
        sig = a.analyze_column(_healthy(), key="s:col")
        assert sig.drifted is False and sig.tier == "cold_start"

    def test_per_source_tier_selected_when_baseline_present(self):
        a = NumericSemanticAnalyzer()
        a.register_baseline("s:col", _batches())
        sig = a.analyze_column(_healthy(), key="s:col")
        assert sig.tier == "per_source"

    def test_unit_error_detected_and_proposal_offered(self):
        a = NumericSemanticAnalyzer()
        a.register_baseline("s:col", _batches())
        bad = _healthy() * 100
        sig = a.analyze_column(bad, key="s:col", column_name="price")
        assert sig.drifted is True
        assert sig.proposal is not None
        assert sig.proposal.transform == "div100"
        assert sig.proposal.z_after < sig.proposal.z_before

    def test_proposal_does_not_mutate_input(self):
        a = NumericSemanticAnalyzer()
        a.register_baseline("s:col", _batches())
        bad = _healthy() * 100
        snapshot = np.array(bad, copy=True)
        a.analyze_column(bad, key="s:col")
        assert np.array_equal(bad, snapshot)  # inference only

    def test_regime_change_detected_but_not_healed(self):
        """Safety property: a legitimate mean/variance shift is flagged but has
        no inverse transform that clears the gate → no heal proposal committed."""
        a = NumericSemanticAnalyzer()
        a.register_baseline("s:col", _batches(mu=500, sd=80))
        mean_shift = _healthy(mu=500, sd=80) + 1.5 * 80
        sig = a.analyze_column(mean_shift, key="s:col")
        if sig.drifted and sig.proposal is not None:
            assert sig.proposal.transform == "none"

    def test_cold_start_uses_prototypes(self):
        protos = TypePrototypes.fit({
            "price": _batches(mu=500, sd=80),
            "prob": [np.round(_RNG.uniform(0, 1, 120), 4) for _ in range(15)],
        })
        a = NumericSemanticAnalyzer(prototypes=protos)
        sig = a.analyze_column(_healthy(mu=500, sd=80), key="new:col")
        assert sig.tier == "cold_start"
        assert sig.nearest_type == "price"

    def test_load_baseline_roundtrip(self):
        b = NumericBaseline.fit(_batches())
        a = NumericSemanticAnalyzer()
        a.load_baseline("s:col", b)
        assert a.has_baseline("s:col")
        assert a.get_baseline("s:col").n_batches == 15


# ── column extraction ───────────────────────────────────────────────────────

class TestNumericColumnsFromRows:
    def test_extracts_numeric_only(self):
        rows = [{"a": 1, "b": "x"}, {"a": 2, "b": "y"}]
        out = numeric_columns_from_rows(rows)
        assert "a" in out and "b" not in out
        assert out["a"] == [1.0, 2.0]

    def test_all_or_nothing_typing(self):
        rows = [{"a": 1}, {"a": "oops"}, {"a": 3}]
        out = numeric_columns_from_rows(rows)
        assert "a" not in out  # one non-numeric poisons the column

    def test_empty_rows(self):
        assert numeric_columns_from_rows([]) == {}

    def test_skips_none_values(self):
        rows = [{"a": 1.0}, {"a": None}, {"a": 3.0}]
        out = numeric_columns_from_rows(rows)
        assert out["a"] == [1.0, 3.0]
