"""
AURA Insights Engine
Auto-generates insights, charts, and narratives from data
"""

import math
import statistics as _stats
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass
from enum import Enum
import json
from datetime import datetime


class ChartType(Enum):
    """Supported chart types"""
    TABLE = "table"
    LINE = "line"
    BAR = "bar"
    SCATTER = "scatter"
    PIE = "pie"
    HISTOGRAM = "histogram"
    BOX = "box"
    HEATMAP = "heatmap"


class InsightType(Enum):
    """Types of insights to generate"""
    TREND = "trend"
    ANOMALY = "anomaly"
    COMPARISON = "comparison"
    DISTRIBUTION = "distribution"
    CORRELATION = "correlation"
    OUTLIER = "outlier"


@dataclass
class Insight:
    """An individual insight"""
    type: InsightType
    title: str
    description: str
    metric_name: Optional[str] = None
    metric_value: Optional[float] = None
    metric_change: Optional[float] = None
    supporting_data: Optional[Dict[str, Any]] = None
    confidence: float = 0.95


@dataclass
class ChartSpec:
    """Chart specification"""
    type: ChartType
    title: str
    x_axis: str
    y_axis: str
    data: List[Dict[str, Any]]
    config: Dict[str, Any]


class InsightsEngine:
    """Generate insights from query results"""

    def __init__(self):
        self.insights: List[Insight] = []
        self.charts: List[ChartSpec] = []

    def analyze(
        self,
        query: str,
        results: List[Dict[str, Any]],
        column_profiles: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Analyze query results and generate insights

        Args:
            query: Original SQL query
            results: Query results
            column_profiles: Optional column statistics

        Returns:
            Dictionary with insights and chart specs
        """
        self.insights = []
        self.charts = []

        if not results:
            return {"insights": [], "charts": []}

        # Analyze columns
        columns = list(results[0].keys()) if results else []

        # Detect column types
        column_types = self._detect_column_types(results, columns)

        # Generate appropriate charts
        self._generate_charts(results, columns, column_types)

        # Generate insights
        self._generate_insights(results, columns, column_types)

        # Generate narrative
        narrative = self._generate_narrative(query, results, column_types)

        return {
            "insights": [
                {
                    "type": i.type.value,
                    "title": i.title,
                    "description": i.description,
                    "metric_name": i.metric_name,
                    "metric_value": i.metric_value,
                    "metric_change": i.metric_change,
                    "confidence": i.confidence,
                }
                for i in self.insights
            ],
            "charts": [self._chart_to_dict(c) for c in self.charts],
            "narrative": narrative,
            "row_count": len(results),
        }

    def _detect_column_types(
        self,
        results: List[Dict[str, Any]],
        columns: List[str],
    ) -> Dict[str, str]:
        """Detect data types of columns"""
        types = {}

        for col in columns:
            values = [r.get(col) for r in results if r.get(col) is not None]
            if not values:
                types[col] = "unknown"
                continue

            first_val = values[0]

            if isinstance(first_val, (int, float)):
                types[col] = "numeric"
            elif isinstance(first_val, bool):
                types[col] = "boolean"
            elif isinstance(first_val, str):
                # Check if it looks like a date
                if any(d in str(first_val) for d in ["-", "/"]) and len(str(first_val)) < 20:
                    types[col] = "date"
                else:
                    types[col] = "string"
            else:
                types[col] = "object"

        return types

    def _generate_charts(
        self,
        results: List[Dict[str, Any]],
        columns: List[str],
        column_types: Dict[str, str],
    ) -> None:
        """Generate appropriate charts from results"""
        if not results or not columns:
            return

        numeric_cols = [c for c in columns if column_types.get(c) == "numeric"]
        string_cols = [c for c in columns if column_types.get(c) == "string"]
        date_cols = [c for c in columns if column_types.get(c) == "date"]

        # Single numeric column → histogram
        if len(numeric_cols) == 1 and len(columns) == 1:
            self.charts.append(
                ChartSpec(
                    type=ChartType.HISTOGRAM,
                    title=f"Distribution of {numeric_cols[0]}",
                    x_axis=numeric_cols[0],
                    y_axis="frequency",
                    data=results,
                    config={"bins": 20},
                )
            )

        # Date + numeric → line chart
        elif date_cols and numeric_cols:
            self.charts.append(
                ChartSpec(
                    type=ChartType.LINE,
                    title=f"{numeric_cols[0]} Over Time",
                    x_axis=date_cols[0],
                    y_axis=numeric_cols[0],
                    data=results,
                    config={"responsive": True},
                )
            )

        # String + numeric → bar chart
        elif string_cols and numeric_cols:
            self.charts.append(
                ChartSpec(
                    type=ChartType.BAR,
                    title=f"{numeric_cols[0]} by {string_cols[0]}",
                    x_axis=string_cols[0],
                    y_axis=numeric_cols[0],
                    data=results,
                    config={"stacked": False},
                )
            )

        # Multiple numeric columns → scatter
        elif len(numeric_cols) >= 2:
            self.charts.append(
                ChartSpec(
                    type=ChartType.SCATTER,
                    title=f"{numeric_cols[0]} vs {numeric_cols[1]}",
                    x_axis=numeric_cols[0],
                    y_axis=numeric_cols[1],
                    data=results,
                    config={"showline": False},
                )
            )

        # Fallback to table
        if not self.charts:
            self.charts.append(
                ChartSpec(
                    type=ChartType.TABLE,
                    title="Query Results",
                    x_axis="",
                    y_axis="",
                    data=results,
                    config={},
                )
            )

    def _generate_insights(
        self,
        results: List[Dict[str, Any]],
        columns: List[str],
        column_types: Dict[str, str],
    ) -> None:
        """Generate statistical insights from results."""
        if not results:
            return

        numeric_cols = [c for c in columns if column_types.get(c) == "numeric"]

        col_values: Dict[str, List[float]] = {}
        for col in numeric_cols[:6]:
            vals = [float(r[col]) for r in results if r.get(col) is not None]
            if vals:
                col_values[col] = vals

        for col, values in col_values.items():
            n = len(values)
            mean = _stats.mean(values)
            min_val = min(values)
            max_val = max(values)

            desc = f"Count: {n}, Mean: {mean:.2f}, Min: {min_val:.2f}, Max: {max_val:.2f}"
            if n >= 2:
                std = _stats.stdev(values)
                median = _stats.median(values)
                desc += f", Median: {median:.2f}, Std: {std:.2f}"

            self.insights.append(
                Insight(
                    type=InsightType.DISTRIBUTION,
                    title=f"{col} Summary",
                    description=desc,
                    metric_name=col,
                    metric_value=mean,
                )
            )

            # IQR-based outlier detection
            if n >= 4:
                sorted_v = sorted(values)
                q1 = sorted_v[int(n * 0.25)]
                q3 = sorted_v[int(n * 0.75)]
                iqr = q3 - q1
                if iqr > 0:
                    lower, upper = q1 - 1.5 * iqr, q3 + 1.5 * iqr
                    outliers = [v for v in values if v < lower or v > upper]
                    if outliers:
                        self.insights.append(
                            Insight(
                                type=InsightType.OUTLIER,
                                title=f"Outliers in {col}",
                                description=(
                                    f"{len(outliers)} value(s) outside IQR bounds "
                                    f"[{lower:.2f}, {upper:.2f}]"
                                ),
                                confidence=0.85,
                            )
                        )

            # Trend detection: compare first-half mean vs second-half mean
            if n >= 6:
                mid = n // 2
                first_mean = _stats.mean(values[:mid])
                second_mean = _stats.mean(values[mid:])
                change_pct = ((second_mean - first_mean) / first_mean * 100) if first_mean else 0
                if abs(change_pct) >= 10:
                    direction = "increased" if change_pct > 0 else "decreased"
                    self.insights.append(
                        Insight(
                            type=InsightType.TREND,
                            title=f"{col} Trend",
                            description=(
                                f"{col} {direction} by {abs(change_pct):.1f}% "
                                f"(from {first_mean:.2f} to {second_mean:.2f})"
                            ),
                            metric_name=col,
                            metric_value=second_mean,
                            metric_change=round(change_pct, 2),
                            confidence=0.75,
                        )
                    )

        # Pairwise correlations for first 4 numeric columns
        num_cols = list(col_values.keys())[:4]
        for i in range(len(num_cols)):
            for j in range(i + 1, len(num_cols)):
                a, b = num_cols[i], num_cols[j]
                xa, xb = col_values[a], col_values[b]
                n = min(len(xa), len(xb))
                if n < 3:
                    continue
                xa, xb = xa[:n], xb[:n]
                mx, my = _stats.mean(xa), _stats.mean(xb)
                num = sum((xi - mx) * (yi - my) for xi, yi in zip(xa, xb))
                den_a = math.sqrt(sum((xi - mx) ** 2 for xi in xa))
                den_b = math.sqrt(sum((yi - my) ** 2 for yi in xb))
                if den_a > 0 and den_b > 0:
                    corr = num / (den_a * den_b)
                    if abs(corr) >= 0.6:
                        strength = "strong" if abs(corr) >= 0.8 else "moderate"
                        direction = "positive" if corr > 0 else "negative"
                        self.insights.append(
                            Insight(
                                type=InsightType.CORRELATION,
                                title=f"Correlation: {a} & {b}",
                                description=(
                                    f"{strength.capitalize()} {direction} correlation "
                                    f"(r={corr:.3f}) between {a} and {b}"
                                ),
                                confidence=round(abs(corr), 2),
                            )
                        )

    def _generate_narrative(
        self,
        query: str,
        results: List[Dict[str, Any]],
        column_types: Dict[str, str],
    ) -> str:
        """Generate human-readable narrative of results."""
        if not results:
            return "No data to analyze."

        columns = list(results[0].keys())
        numeric_cols = [c for c in columns if column_types.get(c) == "numeric"]
        string_cols = [c for c in columns if column_types.get(c) == "string"]
        date_cols = [c for c in columns if column_types.get(c) == "date"]

        parts = [f"Analysis of {len(results):,} records across {len(columns)} column(s)."]

        if date_cols:
            parts.append(f"Time dimension: {date_cols[0]}.")
        if string_cols:
            parts.append(f"Categorical dimensions: {', '.join(string_cols[:3])}.")
        if numeric_cols:
            parts.append(f"Numeric metrics: {', '.join(numeric_cols[:3])}.")

        # Summarize key insights
        dist_insights = [i for i in self.insights if i.type == InsightType.DISTRIBUTION]
        trend_insights = [i for i in self.insights if i.type == InsightType.TREND]
        outlier_insights = [i for i in self.insights if i.type == InsightType.OUTLIER]
        corr_insights = [i for i in self.insights if i.type == InsightType.CORRELATION]

        if dist_insights:
            parts.append(f"Key metric — {dist_insights[0].description}.")
        if trend_insights:
            parts.append(f"Trend detected: {trend_insights[0].description}.")
        if outlier_insights:
            parts.append(f"Note: {outlier_insights[0].description}.")
        if corr_insights:
            parts.append(f"Relationship: {corr_insights[0].description}.")

        return " ".join(parts)

    def _chart_to_dict(self, chart: ChartSpec) -> Dict[str, Any]:
        """Convert chart to dictionary"""
        return {
            "type": chart.type.value,
            "title": chart.title,
            "x_axis": chart.x_axis,
            "y_axis": chart.y_axis,
            "data": chart.data,
            "config": chart.config,
        }


class AnomalyDetector:
    """Detect anomalies in time-series data"""

    @staticmethod
    def detect_anomalies(
        values: List[float],
        threshold: float = 2.0,
    ) -> List[Tuple[int, float]]:
        """
        Detect anomalies using simple z-score method

        Args:
            values: List of numeric values
            threshold: Standard deviation threshold

        Returns:
            List of (index, z_score) tuples for anomalous values
        """
        if len(values) < 2:
            return []

        mean = sum(values) / len(values)
        variance = sum((x - mean) ** 2 for x in values) / len(values)
        std_dev = variance ** 0.5

        if std_dev == 0:
            return []

        anomalies = []
        for i, val in enumerate(values):
            z_score = abs((val - mean) / std_dev)
            if z_score > threshold:
                anomalies.append((i, z_score))

        return anomalies


class AlertGenerator:
    """Generate alerts based on data thresholds"""

    @staticmethod
    def generate_alerts(
        results: List[Dict[str, Any]],
        rules: Optional[List[Dict[str, Any]]] = None,
    ) -> List[Dict[str, Any]]:
        """Generate alerts based on rules"""
        alerts = []

        if not rules:
            return alerts

        for rule in rules:
            metric = rule.get("metric")
            operator = rule.get("operator")  # ">=", "<=", "<", ">", "=="
            threshold = rule.get("threshold")
            alert_name = rule.get("name", f"Alert: {metric} {operator} {threshold}")

            for i, row in enumerate(results):
                if metric not in row:
                    continue

                value = row[metric]
                triggered = False

                if operator == ">=" and value >= threshold:
                    triggered = True
                elif operator == "<=" and value <= threshold:
                    triggered = True
                elif operator == ">" and value > threshold:
                    triggered = True
                elif operator == "<" and value < threshold:
                    triggered = True
                elif operator == "==" and value == threshold:
                    triggered = True

                if triggered:
                    alerts.append({
                        "rule_name": alert_name,
                        "metric": metric,
                        "value": value,
                        "threshold": threshold,
                        "row_index": i,
                        "timestamp": datetime.now().isoformat(),
                    })

        return alerts
