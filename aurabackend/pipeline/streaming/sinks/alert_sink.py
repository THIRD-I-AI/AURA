"""
Alert Sink – Fire alerts when aggregations cross thresholds
=============================================================
Evaluates simple threshold rules and logs / pushes warnings.
"""
from __future__ import annotations

import json
import logging
from collections import deque
from typing import Any, Deque, Dict, List

from pipeline.streaming.models import WindowState
from pipeline.streaming.sinks.base import BaseSink

logger = logging.getLogger("aura.streaming.sink.alert")

# BUG-091: a long-running pipeline with a frequently-firing rule accumulated
# one entry per alert for the sink's entire lifetime with no cap -- days of
# uptime meant unbounded memory growth. Keep only the most recent alerts.
_DEFAULT_MAX_FIRED = 1000


class AlertSink(BaseSink):
    """
    Config example
    {
        "rules": [
            {"field": "sum_amount", "operator": ">", "threshold": 10000, "label": "High spend window"},
            {"field": "event_count", "operator": ">=", "threshold": 500, "label": "Volume spike"}
        ],
        "max_fired": 1000
    }
    """

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self._rules: List[Dict[str, Any]] = config.get("rules", [])
        max_fired = int(config.get("max_fired", _DEFAULT_MAX_FIRED))
        self._fired: Deque[Dict[str, Any]] = deque(maxlen=max_fired)

    @property
    def fired_alerts(self) -> List[Dict[str, Any]]:
        return list(self._fired)

    async def start(self) -> None:
        self._running = True
        logger.info("Alert sink started with %d rules", len(self._rules))

    async def stop(self) -> None:
        self._running = False
        logger.info("Alert sink stopped (%d alerts fired total)", len(self._fired))

    async def emit_window(self, window: WindowState, pipeline_id: str) -> None:
        for rule in self._rules:
            field = rule["field"]
            op = rule.get("operator", ">")
            threshold = float(rule["threshold"])
            label = rule.get("label", field)

            # Check aggregations first, then top-level window attrs
            value = window.aggregations.get(field)
            if value is None and hasattr(window, field):
                value = getattr(window, field)
            if value is None:
                continue

            triggered = False
            val = float(value)
            if op == ">" and val > threshold:
                triggered = True
            elif op == ">=" and val >= threshold:
                triggered = True
            elif op == "<" and val < threshold:
                triggered = True
            elif op == "<=" and val <= threshold:
                triggered = True
            elif op == "==" and val == threshold:
                triggered = True

            if triggered:
                alert = {
                    "pipeline_id": pipeline_id,
                    "label": label,
                    "field": field,
                    "value": val,
                    "threshold": threshold,
                    "operator": op,
                    "window_key": window.window_key,
                    "window_start": window.window_start,
                    "window_end": window.window_end,
                }
                self._fired.append(alert)
                logger.warning("🚨 ALERT [%s]: %s=%s %s %s  (window=%s)",
                               label, field, val, op, threshold, window.window_key)
