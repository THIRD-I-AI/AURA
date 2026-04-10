"""
AURA Insights Module
Auto-insights generation, anomaly detection, and alerting
"""

from .engine import (
    AlertGenerator,
    AnomalyDetector,
    ChartSpec,
    ChartType,
    Insight,
    InsightsEngine,
    InsightType,
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
