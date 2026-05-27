"""
Sprint S31a — Causal service tests.

Tier A (pure Python, no optional deps beyond pandas/numpy).

Covers:
  * Pydantic model validation (DataSource, CausalDiscoverRequest, Attribution, etc.)
  * _load() mutual exclusivity validation
  * check_stationarity() with synthetic data
  * _correlation_attribute() partial-correlation fallback
  * summarise() text generation
  * attribute() method resolution (auto/gcm/correlation)
"""
from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from causal_service.models import (
    Attribution,
    CausalDiscoverRequest,
    CausalDiscoverResponse,
    DataSource,
    StationarityVerdict,
)

# ── Schema tests ────────────────────────────────────────────────────

class TestDataSource:
    def test_rows_only(self):
        ds = DataSource(rows=[{"a": 1}, {"a": 2}])
        assert ds.duckdb_table is None

    def test_duckdb_only(self):
        ds = DataSource(duckdb_table="metrics")
        assert ds.rows is None

    def test_limit_default(self):
        ds = DataSource(rows=[{"a": 1}])
        assert ds.limit == 10_000

    def test_limit_bounds(self):
        with pytest.raises(Exception):
            DataSource(rows=[{"a": 1}], limit=0)
        with pytest.raises(Exception):
            DataSource(rows=[{"a": 1}], limit=500_001)

    def test_extra_field_rejected(self):
        with pytest.raises(Exception):
            DataSource(rows=[{"a": 1}], unknown="x")


class TestAttribution:
    def test_basic(self):
        a = Attribution(cause="temperature", score=0.85)
        assert a.confidence == 0.0
        assert a.direction == "unknown"

    def test_confidence_bounds(self):
        with pytest.raises(Exception):
            Attribution(cause="x", score=0.5, confidence=1.5)

    def test_full(self):
        a = Attribution(cause="x", score=0.9, confidence=0.8, direction="positive")
        assert a.direction == "positive"


class TestStationarityVerdict:
    def test_stationary(self):
        sv = StationarityVerdict(stationary=True)
        assert sv.adf_p_value is None
        assert sv.reasons == []

    def test_non_stationary(self):
        sv = StationarityVerdict(
            stationary=False,
            adf_p_value=0.42,
            split_drift_sigma=4.5,
            reasons=["ADF failed", "drift detected"],
        )
        assert not sv.stationary
        assert len(sv.reasons) == 2


class TestCausalDiscoverRequest:
    def test_minimal(self):
        req = CausalDiscoverRequest(
            target_metric="revenue",
            training_data=DataSource(rows=[{"revenue": 100, "temp": 20}]),
            anomaly_data=DataSource(rows=[{"revenue": 500, "temp": 40}]),
        )
        assert req.method == "auto"
        assert req.top_k == 5
        assert req.enforce_stationarity is True

    def test_method_override(self):
        req = CausalDiscoverRequest(
            target_metric="y",
            training_data=DataSource(rows=[{"y": 1}]),
            anomaly_data=DataSource(rows=[{"y": 2}]),
            method="correlation",
        )
        assert req.method == "correlation"


class TestCausalDiscoverResponse:
    def test_full(self):
        resp = CausalDiscoverResponse(
            target_metric="revenue",
            method_used="correlation",
            sample_count=100,
            anomaly_count=5,
            attributions=[Attribution(cause="temp", score=0.8)],
            summary="Temperature is the top contributor.",
        )
        assert len(resp.attributions) == 1
        assert resp.warnings == []
        assert resp.stationarity is None


# ── _load() validation ──────────────────────────────────────────────

class TestLoad:
    def test_rows_and_duckdb_both_raises(self):
        from fastapi import HTTPException

        from causal_service.main import _load
        ds = DataSource(rows=[{"a": 1}], duckdb_table="t")
        with pytest.raises(HTTPException) as exc_info:
            _load(ds, "training")
        assert exc_info.value.status_code == 400
        assert "not both" in exc_info.value.detail

    def test_neither_rows_nor_duckdb_raises(self):
        from fastapi import HTTPException

        from causal_service.main import _load
        ds = DataSource()
        with pytest.raises(HTTPException) as exc_info:
            _load(ds, "anomaly")
        assert exc_info.value.status_code == 400

    def test_empty_rows_raises(self):
        from fastapi import HTTPException

        from causal_service.main import _load
        ds = DataSource(rows=[])
        with pytest.raises(HTTPException) as exc_info:
            _load(ds, "training")
        assert exc_info.value.status_code == 400
        assert "empty" in exc_info.value.detail

    def test_valid_rows(self):
        from causal_service.main import _load
        ds = DataSource(rows=[{"x": 1, "y": 2}, {"x": 3, "y": 4}])
        df = _load(ds, "training")
        assert isinstance(df, pd.DataFrame)
        assert len(df) == 2
        assert list(df.columns) == ["x", "y"]


# ── check_stationarity ─────────────────────────────────────────────

class TestCheckStationarity:
    def test_small_sample_accepted(self):
        from causal_service.discovery import check_stationarity
        s = pd.Series([1.0, 2.0, 3.0])
        v = check_stationarity(s)
        assert v.stationary is True
        assert "not tested" in v.reasons[0]

    def test_stationary_white_noise(self):
        from causal_service.discovery import check_stationarity
        rng = np.random.RandomState(42)
        s = pd.Series(rng.normal(0, 1, 200))
        v = check_stationarity(s)
        assert v.stationary is True

    def test_non_stationary_trend(self):
        from causal_service.discovery import check_stationarity
        s = pd.Series(np.arange(200, dtype=float))
        v = check_stationarity(s)
        assert v.stationary is False


# ── attribute() method resolution ───────────────────────────────────

class TestAttribute:
    def _make_data(self, n=100):
        rng = np.random.RandomState(42)
        x1 = rng.normal(0, 1, n)
        x2 = rng.normal(0, 1, n)
        y = 2.0 * x1 + 0.5 * x2 + rng.normal(0, 0.1, n)
        return pd.DataFrame({"x1": x1, "x2": x2, "y": y})

    def test_correlation_fallback(self):
        from causal_service.discovery import attribute
        df = self._make_data()
        attrs, method, warnings, verdict = attribute(
            training=df, anomalies=df.iloc[:5],
            target="y", candidates=["x1", "x2"],
            edges=None, method="correlation", top_k=5,
        )
        assert method == "correlation"
        assert len(attrs) >= 1
        assert attrs[0].cause == "x1"
        assert attrs[0].score > attrs[1].score
        assert verdict is None

    def test_drops_non_numeric_candidates(self):
        from causal_service.discovery import attribute
        df = self._make_data()
        df["label"] = "category"
        attrs, method, warnings, _ = attribute(
            training=df, anomalies=df.iloc[:5],
            target="y", candidates=["x1", "x2", "label"],
            edges=None, method="correlation", top_k=5,
        )
        assert any("non-numeric" in w for w in warnings)
        assert all(a.cause != "label" for a in attrs)

    def test_no_numeric_candidates_returns_empty(self):
        from causal_service.discovery import attribute
        df = pd.DataFrame({"y": [1, 2, 3], "cat": ["a", "b", "c"]})
        attrs, method, warnings, _ = attribute(
            training=df, anomalies=df.iloc[:1],
            target="y", candidates=["cat"],
            edges=None, method="correlation", top_k=5,
        )
        assert attrs == []
        assert method == "none"

    def test_auto_resolves_to_correlation_without_dowhy(self):
        from causal_service.discovery import _DOWHY_AVAILABLE, attribute
        df = self._make_data(50)
        attrs, method, warnings, _ = attribute(
            training=df, anomalies=df.iloc[:5],
            target="y", candidates=["x1", "x2"],
            edges=None, method="auto", top_k=5,
        )
        if not _DOWHY_AVAILABLE:
            assert method == "correlation"
        else:
            assert method in ("gcm", "correlation")


# ── summarise() ─────────────────────────────────────────────────────

class TestSummarise:
    def test_basic(self):
        from causal_service.discovery import summarise
        attrs = [
            Attribution(cause="x1", score=0.8, direction="positive"),
            Attribution(cause="x2", score=0.3, direction="negative"),
        ]
        text = summarise(attrs, "revenue", "correlation")
        assert "x1" in text
        assert "revenue" in text
        assert isinstance(text, str)
        assert len(text) > 10
