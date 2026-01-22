"""
AURA Insights Engine
Auto-generates insights, charts, and narratives from data
"""

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
        """Generate insights from results"""
        if not results:
            return

        numeric_cols = [c for c in columns if column_types.get(c) == "numeric"]

        # Summary statistics
        for col in numeric_cols[:1]:  # Analyze first numeric column
            values = [float(r.get(col, 0)) for r in results if r.get(col) is not None]
            if values:
                avg = sum(values) / len(values)
                min_val = min(values)
                max_val = max(values)
                range_val = max_val - min_val

                self.insights.append(
                    Insight(
                        type=InsightType.DISTRIBUTION,
                        title=f"{col} Summary",
                        description=f"Average: {avg:.2f}, Range: {min_val:.2f} - {max_val:.2f}",
                        metric_name=col,
                        metric_value=avg,
                    )
                )

                # Outlier detection
                if range_val > 0:
                    outliers = [v for v in values if v < min_val + range_val * 0.1 or v > max_val - range_val * 0.1]
                    if outliers:
                        self.insights.append(
                            Insight(
                                type=InsightType.OUTLIER,
                                title=f"Outliers Detected in {col}",
                                description=f"Found {len(outliers)} values outside normal range",
                                confidence=0.8,
                            )
                        )

    def _generate_narrative(
        self,
        query: str,
        results: List[Dict[str, Any]],
        column_types: Dict[str, str],
    ) -> str:
        """Generate human-readable narrative of results"""
        if not results:
            return "No data to analyze."

        narrative_parts = []
        narrative_parts.append(f"Analysis of {len(results)} records")

        # Describe columns
        columns = list(results[0].keys())
        numeric_cols = [c for c in columns if column_types.get(c) == "numeric"]
        string_cols = [c for c in columns if column_types.get(c) == "string"]

        if numeric_cols:
            narrative_parts.append(f"with {len(numeric_cols)} numeric metric(s)")
        if string_cols:
            narrative_parts.append(f"and {len(string_cols)} categorical dimension(s)")

        narrative_parts.append(".")

        # Add insights summary
        if self.insights:
            narrative_parts.append(f"Key findings: {self.insights[0].description}")

        return " ".join(narrative_parts)

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
