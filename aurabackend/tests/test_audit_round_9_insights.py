"""Audit round 9, insights area: BUG-509 .. BUG-512."""
from __future__ import annotations

import asyncio

from insights_service.engine import ChartType, InsightsEngine, InsightType
from insights_service.main import AnalyzeRequest, _suggest_charts, analyze_results


def _trend(values):
    engine = InsightsEngine()
    engine.analyze("q", [{"net": v} for v in values])
    trends = [i for i in engine.insights if i.type == InsightType.TREND]
    return trends[0] if trends else None


# ── BUG-509 ─────────────────────────────────────────────────────────────────

def test_bug509_shrinking_loss_is_reported_as_an_increase():
    t = _trend([-100, -100, -100, -50, -50, -50])
    assert t is not None
    # -100 -> -50 is +50 in absolute terms: +50% of the baseline's magnitude.
    assert t.metric_change == 50.0
    assert "increased by 50.0%" in t.description


def test_bug509_growing_loss_is_reported_as_a_decrease():
    t = _trend([-50, -50, -50, -100, -100, -100])
    assert t is not None
    assert t.metric_change == -100.0
    assert "decreased" in t.description


def test_bug509_positive_baseline_unchanged():
    t = _trend([100, 100, 100, 150, 150, 150])
    assert t is not None
    assert t.metric_change == 50.0
    assert "increased by 50.0%" in t.description


def test_bug509_trend_from_a_zero_baseline_is_still_reported():
    t = _trend([0, 0, 0, 500, 500, 500])
    assert t is not None
    assert "increased" in t.description
    assert "500.00" in t.description
    # A percentage change from 0 is undefined, so none is claimed.
    assert t.metric_change is None
    assert "%" not in t.description


def test_bug509_flat_zero_series_reports_no_trend():
    assert _trend([0, 0, 0, 0, 0, 0]) is None


# ── BUG-510 ─────────────────────────────────────────────────────────────────

def _types(rows):
    return InsightsEngine()._detect_column_types(rows, list(rows[0].keys()))


def test_bug510_hyphenated_codes_are_not_dates():
    rows = [{"sku": f"SKU-{i}", "revenue": i * 10} for i in range(5)]
    assert _types(rows)["sku"] == "string"
    result = InsightsEngine().analyze("q", rows)
    chart = result["charts"][0]
    assert chart["type"] == ChartType.BAR.value
    assert chart["x_axis"] == "sku"
    assert "Time dimension" not in result["narrative"]


def test_bug510_first_value_with_dash_does_not_decide_the_column():
    rows = [{"region": "North-East", "sales": 10}, {"region": "West", "sales": 20}]
    assert _types(rows)["region"] == "string"


def test_bug510_slash_values_are_not_dates():
    for v in ("N/A", "A/B", "555-123-4567"):
        assert _types([{"c": v}, {"c": v}])["c"] == "string", v


def test_bug510_real_dates_still_detected():
    for vals in (["2024-01-15", "2024-02-20"], ["01/15/2024", "02/20/2024"],
                 ["2024-01-15 10:30:00", "2024-02-20 11:00:00"], ["2024-01", "2024-02"]):
        assert _types([{"d": v} for v in vals])["d"] == "date", vals


# ── BUG-511 ─────────────────────────────────────────────────────────────────

def test_bug511_boolean_column_is_not_a_numeric_measure():
    s = _suggest_charts(columns=["active", "region"],
                        data_sample=[{"active": True, "region": "East"}])
    assert not any(x.get("yAxis") == "active" or x.get("measure") == "active" for x in s)


def test_bug511_two_flag_columns_get_no_scatter():
    s = _suggest_charts(columns=["a", "b"], data_sample=[{"a": True, "b": False}] * 3)
    assert not any(x["type"] == "scatter" for x in s)


def test_bug511_first_row_none_does_not_drop_a_numeric_column():
    sample = [{"region": "East", "sales": None}, {"region": "West", "sales": 20}]
    s = _suggest_charts(columns=["region", "sales"], data_sample=sample)
    assert any(x["type"] == "bar" and x["yAxis"] == "sales" for x in s)


# ── BUG-512 ─────────────────────────────────────────────────────────────────

def test_bug512_large_result_gets_a_horizontal_bar_and_no_pie():
    rows = [{"category": f"c{i}", "value": i} for i in range(500)]
    resp = asyncio.run(analyze_results(AnalyzeRequest(query="q", results=rows)))
    bars = [s for s in resp.chart_suggestions if s["type"] == "bar"]
    assert bars and bars[0]["orientation"] == "horizontal"
    assert not any(s["type"] == "pie" for s in resp.chart_suggestions)


def test_bug512_small_result_keeps_vertical_bar_and_pie():
    rows = [{"category": f"c{i}", "value": i} for i in range(5)]
    resp = asyncio.run(analyze_results(AnalyzeRequest(query="q", results=rows)))
    bars = [s for s in resp.chart_suggestions if s["type"] == "bar"]
    assert bars and bars[0]["orientation"] == "vertical"
    assert any(s["type"] == "pie" for s in resp.chart_suggestions)
