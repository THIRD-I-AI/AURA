"""
AURA Insights Module
Auto-insights generation, anomaly detection, and alerting
"""

from .engine import (
    InsightsEngine,
    AnomalyDetector,
    AlertGenerator,
    Insight,
    ChartSpec,
    InsightType,
    ChartType,
)

__all__ = [
    "InsightsEngine",
    "AnomalyDetector",
    "AlertGenerator",
    "Insight",
    "ChartSpec",
    "InsightType",
    "ChartType",
]
