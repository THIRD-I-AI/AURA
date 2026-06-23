"""
Insights Engine Unit Tests
============================
Tests for InsightsEngine.analyze(), chart type selection, insight generation
(distribution / trend / outlier / correlation), AnomalyDetector, and AlertGenerator.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from insights_service.engine import (
    AlertGenerator,
    AnomalyDetector,
    ChartType,
    InsightsEngine,
    InsightType,
)

# ── Helpers ───────────────────────────────────────────────────────────────────

def _engine() -> InsightsEngine:
    return InsightsEngine()


# ── Empty / edge cases ────────────────────────────────────────────────────────

class TestAnalyzeEdgeCases:
    def test_empty_results(self):
        result = _engine().analyze("SELECT 1", [])
        assert result["insights"] == []
        assert result["charts"] == []

    def test_single_row(self):
        result = _engine().analyze("SELECT 1", [{"value": 42}])
        assert result["row_count"] == 1
        assert isinstance(result["narrative"], str)

    def test_row_count_in_output(self):
        rows = [{"x": i} for i in range(5)]
        result = _engine().analyze("q", rows)
        assert result["row_count"] == 5

    def test_narrative_is_string(self):
        rows = [{"a": 1, "b": "x"}]
        result = _engine().analyze("q", rows)
        assert isinstance(result["narrative"], str)
        assert len(result["narrative"]) > 0


# ── Column type detection ─────────────────────────────────────────────────────

class TestColumnTypeDetection:
    def _types(self, rows):
        engine = _engine()
        cols = list(rows[0].keys())
        return engine._detect_column_types(rows, cols)

    def test_numeric_detection(self):
        t = self._types([{"v": 3.14}, {"v": 2.71}])
        assert t["v"] == "numeric"

    def test_string_detection(self):
        t = self._types([{"label": "foo"}, {"label": "bar"}])
        assert t["label"] == "string"

    def test_boolean_detection(self):
        # Python bool is a subclass of int, so isinstance(True, (int, float)) is True.
        # The engine checks numeric before boolean, so booleans resolve to "numeric".
        t = self._types([{"flag": True}, {"flag": False}])
        assert t["flag"] == "numeric"

    def test_date_like_detection(self):
        t = self._types([{"dt": "2024-01-15"}, {"dt": "2024-02-20"}])
        assert t["dt"] == "date"

    def test_unknown_for_all_none(self):
        t = self._types([{"x": None}, {"x": None}])
        assert t["x"] == "unknown"


# ── Chart selection ───────────────────────────────────────────────────────────

class TestChartSelection:
    def _charts(self, rows):
        engine = _engine()
        result = engine.analyze("q", rows)
        return result["charts"]

    def test_single_numeric_gives_histogram(self):
        rows = [{"score": i} for i in range(10)]
        charts = self._charts(rows)
        assert any(c["type"] == ChartType.HISTOGRAM.value for c in charts)

    def test_date_plus_numeric_gives_line(self):
        rows = [{"date": "2024-01-01", "revenue": 100},
                {"date": "2024-02-01", "revenue": 200}]
        charts = self._charts(rows)
        assert any(c["type"] == ChartType.LINE.value for c in charts)

    def test_string_plus_numeric_gives_bar(self):
        rows = [{"product": "A", "sales": 10},
                {"product": "B", "sales": 20}]
        charts = self._charts(rows)
        assert any(c["type"] == ChartType.BAR.value for c in charts)

    def test_two_numerics_gives_scatter(self):
        rows = [{"x": i, "y": i * 2} for i in range(5)]
        charts = self._charts(rows)
        assert any(c["type"] == ChartType.SCATTER.value for c in charts)

    def test_fallback_table_for_string_only(self):
        rows = [{"name": "alice"}, {"name": "bob"}]
        charts = self._charts(rows)
        assert any(c["type"] == ChartType.TABLE.value for c in charts)

    def test_at_least_one_chart_always(self):
        for rows in (
            [{"a": 1}],
            [{"a": "x"}],
            [{"a": 1, "b": 2}],
        ):
            assert len(self._charts(rows)) >= 1


# ── Insight generation ────────────────────────────────────────────────────────

class TestInsightGeneration:
    def test_distribution_insight_for_numeric(self):
        rows = [{"revenue": float(v)} for v in range(1, 11)]
        result = _engine().analyze("q", rows)
        types = [i["type"] for i in result["insights"]]
        assert InsightType.DISTRIBUTION.value in types

    def test_distribution_contains_mean(self):
        rows = [{"v": float(i)} for i in [10, 20, 30]]
        result = _engine().analyze("q", rows)
        dist = next(i for i in result["insights"] if i["type"] == InsightType.DISTRIBUTION.value)
        assert "Mean" in dist["description"]
        assert dist["metric_value"] == pytest.approx(20.0)

    def test_outlier_detected(self):
        """A single very large value should trigger an outlier insight."""
        rows = [{"v": float(i)} for i in [10, 11, 10, 12, 11, 10, 1000]]
        result = _engine().analyze("q", rows)
        types = [i["type"] for i in result["insights"]]
        assert InsightType.OUTLIER.value in types

    def test_no_outlier_for_uniform_data(self):
        rows = [{"v": 100.0} for _ in range(8)]
        result = _engine().analyze("q", rows)
        types = [i["type"] for i in result["insights"]]
        assert InsightType.OUTLIER.value not in types

    def test_trend_detected_on_increasing_series(self):
        """Second half much larger than first → trend insight."""
        rows = [{"v": float(i)} for i in [1, 2, 1, 2, 100, 200, 150, 180]]
        result = _engine().analyze("q", rows)
        types = [i["type"] for i in result["insights"]]
        assert InsightType.TREND.value in types

    def test_correlation_detected(self):
        """Perfectly correlated columns should produce a correlation insight."""
        rows = [{"a": float(i), "b": float(i * 2)} for i in range(1, 11)]
        result = _engine().analyze("q", rows)
        types = [i["type"] for i in result["insights"]]
        assert InsightType.CORRELATION.value in types

    def test_no_correlation_for_random_data(self):
        """Uncorrelated columns (one constant) should not trigger correlation."""
        rows = [{"a": 1.0, "b": float(i)} for i in range(8)]
        result = _engine().analyze("q", rows)
        types = [i["type"] for i in result["insights"]]
        assert InsightType.CORRELATION.value not in types

    def test_no_insights_for_non_numeric(self):
        rows = [{"name": "x"}, {"name": "y"}]
        result = _engine().analyze("q", rows)
        assert result["insights"] == []


# ── Narrative ─────────────────────────────────────────────────────────────────

class TestNarrative:
    def test_mentions_row_count(self):
        rows = [{"v": float(i)} for i in range(5)]
        result = _engine().analyze("q", rows)
        assert "5" in result["narrative"]

    def test_mentions_numeric_metric(self):
        rows = [{"revenue": 1.0}, {"revenue": 2.0}]
        result = _engine().analyze("q", rows)
        assert "revenue" in result["narrative"].lower()

    def test_empty_returns_no_narrative_key(self):
        # When results are empty, analyze() short-circuits and returns no narrative key.
        result = _engine().analyze("q", [])
        assert "narrative" not in result


# ── AnomalyDetector ───────────────────────────────────────────────────────────

class TestAnomalyDetector:
    def test_detects_spike(self):
        values = [10.0, 11.0, 10.0, 9.0, 10.0, 10.0, 100.0]
        anomalies = AnomalyDetector.detect_anomalies(values)
        assert len(anomalies) >= 1
        indices = [i for i, _ in anomalies]
        assert 6 in indices  # the 100.0

    def test_no_anomalies_uniform(self):
        values = [5.0] * 10
        assert AnomalyDetector.detect_anomalies(values) == []

    def test_empty_input(self):
        assert AnomalyDetector.detect_anomalies([]) == []

    def test_single_value(self):
        assert AnomalyDetector.detect_anomalies([42.0]) == []

    def test_custom_threshold(self):
        """Lower threshold should detect more anomalies."""
        values = [10.0, 10.0, 10.0, 15.0, 10.0]
        strict = AnomalyDetector.detect_anomalies(values, threshold=1.0)
        loose = AnomalyDetector.detect_anomalies(values, threshold=3.0)
        assert len(strict) >= len(loose)

    def test_z_score_positive(self):
        values = [0.0, 0.0, 0.0, 0.0, 10.0]
        anomalies = AnomalyDetector.detect_anomalies(values)
        z_scores = [z for _, z in anomalies]
        assert all(z > 0 for z in z_scores)


# ── AlertGenerator ────────────────────────────────────────────────────────────

class TestAlertGenerator:
    def test_no_rules_no_alerts(self):
        rows = [{"cpu": 90}]
        alerts = AlertGenerator.generate_alerts(rows, rules=None)
        assert alerts == []

    def test_gte_operator_triggers(self):
        rows = [{"cpu": 95}, {"cpu": 40}]
        rules = [{"metric": "cpu", "operator": ">=", "threshold": 90, "name": "HighCPU"}]
        alerts = AlertGenerator.generate_alerts(rows, rules)
        assert len(alerts) == 1
        assert alerts[0]["rule_name"] == "HighCPU"
        assert alerts[0]["value"] == 95

    def test_lte_operator_triggers(self):
        rows = [{"balance": 5}, {"balance": 100}]
        rules = [{"metric": "balance", "operator": "<=", "threshold": 10}]
        alerts = AlertGenerator.generate_alerts(rows, rules)
        assert len(alerts) == 1
        assert alerts[0]["value"] == 5

    def test_gt_operator(self):
        rows = [{"v": 10}, {"v": 11}]
        rules = [{"metric": "v", "operator": ">", "threshold": 10}]
        alerts = AlertGenerator.generate_alerts(rows, rules)
        assert len(alerts) == 1
        assert alerts[0]["value"] == 11

    def test_lt_operator(self):
        rows = [{"v": 9}, {"v": 10}]
        rules = [{"metric": "v", "operator": "<", "threshold": 10}]
        alerts = AlertGenerator.generate_alerts(rows, rules)
        assert len(alerts) == 1
        assert alerts[0]["value"] == 9

    def test_eq_operator(self):
        rows = [{"status": 0}, {"status": 1}]
        rules = [{"metric": "status", "operator": "==", "threshold": 0}]
        alerts = AlertGenerator.generate_alerts(rows, rules)
        assert len(alerts) == 1

    def test_multiple_rules(self):
        rows = [{"cpu": 95, "mem": 85}]
        rules = [
            {"metric": "cpu", "operator": ">=", "threshold": 90},
            {"metric": "mem", "operator": ">=", "threshold": 80},
        ]
        alerts = AlertGenerator.generate_alerts(rows, rules)
        assert len(alerts) == 2

    def test_missing_metric_skipped(self):
        rows = [{"other": 100}]
        rules = [{"metric": "cpu", "operator": ">=", "threshold": 50}]
        alerts = AlertGenerator.generate_alerts(rows, rules)
        assert alerts == []

    def test_alert_contains_row_index(self):
        rows = [{"v": 5}, {"v": 100}, {"v": 3}]
        rules = [{"metric": "v", "operator": ">", "threshold": 50}]
        alerts = AlertGenerator.generate_alerts(rows, rules)
        assert alerts[0]["row_index"] == 1

    def test_alert_has_timestamp(self):
        rows = [{"v": 99}]
        rules = [{"metric": "v", "operator": ">", "threshold": 0}]
        alerts = AlertGenerator.generate_alerts(rows, rules)
        assert "timestamp" in alerts[0]
