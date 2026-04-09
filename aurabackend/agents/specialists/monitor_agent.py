"""
Monitor Agent
==============
Continuously watches pipeline health, data quality, and service
availability. When it detects degradation it:

  1. Logs a structured alert
  2. Automatically submits the affected batch to the UASR ingest
     endpoint to trigger self-healing
  3. Records the event in the evolution engine for learning

The agent also surfaces metrics that the frontend dashboard can display
in real time via the standard SSE progress callback.
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx

from agents.base import AgentContext, AgentResult, AgentStatus, BaseAgent

logger = logging.getLogger("agents.monitor")

_UASR_URL = "http://localhost:8009"
_GATEWAY_URL = "http://localhost:8000"
_TIMEOUT = 15.0


class MonitorAgent(BaseAgent):
    """
    Multi-purpose health monitor.

    Input context keys (via ctx.metadata):
      - pipelines: list of {pipeline_id, source_id, connection_id}
      - data_batches: list of {source_id, columns, rows} to check for drift
      - check_services: bool — if True, polls all microservices for liveness
      - thresholds: {null_rate: float, row_count_drop_pct: float, latency_ms: float}
    """

    name = "MonitorAgent"
    description = (
        "Monitors pipeline health, data quality, and service availability. "
        "Automatically triggers UASR self-healing on anomaly detection."
    )

    async def _run(self, ctx: AgentContext, result: AgentResult) -> AgentResult:
        meta = ctx.metadata or {}
        thresholds = meta.get("thresholds", {})
        alerts: List[Dict[str, Any]] = []

        # ── 1. Service health check ──────────────────────────────
        if meta.get("check_services", True):
            result.add_step(action="service_health_check",
                            input_summary="Polling all microservices")
            service_alerts = await self._check_services()
            alerts.extend(service_alerts)
            result.add_step(
                action="service_health_result",
                output_summary=f"{len(service_alerts)} service alert(s)",
            )

        # ── 2. Data quality / drift check ────────────────────────
        batches = meta.get("data_batches", [])
        if batches:
            result.add_step(action="data_quality_check",
                            input_summary=f"Checking {len(batches)} data batch(es)")
            drift_alerts = await self._check_data_quality(batches, thresholds)
            alerts.extend(drift_alerts)
            result.add_step(
                action="drift_check_result",
                output_summary=f"{len(drift_alerts)} drift alert(s)",
            )

        # ── 3. Pipeline execution health ─────────────────────────
        pipelines = meta.get("pipelines", [])
        if pipelines:
            result.add_step(action="pipeline_health_check",
                            input_summary=f"Checking {len(pipelines)} pipeline(s)")
            pipe_alerts = await self._check_pipelines(pipelines, thresholds)
            alerts.extend(pipe_alerts)
            result.add_step(
                action="pipeline_health_result",
                output_summary=f"{len(pipe_alerts)} pipeline alert(s)",
            )

        # ── 4. Record in evolution engine ────────────────────────
        if alerts:
            await self._record_alerts(alerts, ctx)

        severity = "healthy" if not alerts else (
            "critical" if any(a.get("severity") == "critical" for a in alerts)
            else "degraded"
        )

        result.status = AgentStatus.SUCCESS
        result.output["alerts"] = alerts
        result.output["alert_count"] = len(alerts)
        result.output["system_severity"] = severity
        result.output["checked_at"] = datetime.now(timezone.utc).isoformat()
        return result

    # ── Service checks ─────────────────────────────────────────────

    async def _check_services(self) -> List[Dict[str, Any]]:
        services = {
            "api_gateway": "http://localhost:8000/health",
            "code_generation": "http://localhost:8001/health",
            "database_service": "http://localhost:8002/health",
            "execution_sandbox": "http://localhost:8003/health",
            "scheduler": "http://localhost:8004/health",
            "insights": "http://localhost:8005/health",
            "metadata_store": "http://localhost:8007/health",
            "uasr": "http://localhost:8009/health",
        }

        alerts: List[Dict[str, Any]] = []
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            tasks = {name: client.get(url) for name, url in services.items()}
            results = await asyncio.gather(
                *[v for v in tasks.values()], return_exceptions=True
            )

        for (name, _), response in zip(services.items(), results):
            if isinstance(response, Exception):
                alerts.append({
                    "type": "service_down",
                    "service": name,
                    "severity": "critical",
                    "message": f"Service '{name}' is unreachable: {response}",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })
            elif response.status_code != 200:
                alerts.append({
                    "type": "service_unhealthy",
                    "service": name,
                    "severity": "high",
                    "message": f"Service '{name}' returned HTTP {response.status_code}",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })

        return alerts

    # ── Data quality checks ────────────────────────────────────────

    async def _check_data_quality(
        self,
        batches: List[Dict[str, Any]],
        thresholds: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        alerts: List[Dict[str, Any]] = []
        null_threshold = thresholds.get("null_rate", 0.3)  # 30% nulls = alert

        async with httpx.AsyncClient(timeout=30.0) as client:
            for batch in batches:
                source_id = batch.get("source_id", "unknown")
                rows = batch.get("rows", [])
                columns = batch.get("columns", list(rows[0].keys()) if rows else [])

                if not rows:
                    continue

                # Local null-rate check (fast, no network)
                for col in columns:
                    null_count = sum(1 for r in rows if r.get(col) is None)
                    null_rate = null_count / len(rows)
                    if null_rate > null_threshold:
                        alerts.append({
                            "type": "high_null_rate",
                            "source_id": source_id,
                            "column": col,
                            "null_rate": round(null_rate, 3),
                            "severity": "high" if null_rate > 0.5 else "medium",
                            "message": f"Column '{col}' has {null_rate:.1%} null values",
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                        })

                # Submit to UASR for full drift detection
                try:
                    resp = await client.post(
                        f"{_UASR_URL}/uasr/ingest",
                        json={
                            "source_id": source_id,
                            "columns": columns,
                            "rows": rows[:500],  # cap to 500 rows for speed
                        },
                        timeout=20.0,
                    )
                    if resp.status_code == 200:
                        data = resp.json()
                        if data.get("drift_detected"):
                            alerts.append({
                                "type": "data_drift",
                                "source_id": source_id,
                                "drift_type": data.get("drift_type"),
                                "severity": data.get("severity", "medium"),
                                "recovery_id": data.get("recovery_id"),
                                "shim_deployed": data.get("shim_deployed", False),
                                "message": (
                                    f"Data drift detected in '{source_id}': "
                                    f"{data.get('drift_type')} ({data.get('severity')}). "
                                    f"Recovery {'deployed' if data.get('shim_deployed') else 'attempted'}."
                                ),
                                "timestamp": datetime.now(timezone.utc).isoformat(),
                            })
                except Exception as exc:
                    logger.warning("UASR ingest failed for %s: %s", source_id, exc)

        return alerts

    # ── Pipeline health checks ─────────────────────────────────────

    async def _check_pipelines(
        self,
        pipelines: List[Dict[str, Any]],
        thresholds: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        alerts: List[Dict[str, Any]] = []
        latency_threshold = thresholds.get("latency_ms", 10_000)

        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            for pipe in pipelines:
                pipeline_id = pipe.get("pipeline_id")
                if not pipeline_id:
                    continue
                try:
                    t0 = time.monotonic()
                    resp = await client.get(
                        f"{_GATEWAY_URL}/streaming/pipelines/{pipeline_id}/metrics"
                    )
                    latency_ms = (time.monotonic() - t0) * 1000

                    if resp.status_code == 200:
                        metrics = resp.json()
                        status = metrics.get("status", "unknown")

                        if status == "failed":
                            alerts.append({
                                "type": "pipeline_failed",
                                "pipeline_id": pipeline_id,
                                "severity": "critical",
                                "message": f"Pipeline '{pipeline_id}' is in FAILED state",
                                "metrics": metrics,
                                "timestamp": datetime.now(timezone.utc).isoformat(),
                            })
                        elif latency_ms > latency_threshold:
                            alerts.append({
                                "type": "pipeline_slow",
                                "pipeline_id": pipeline_id,
                                "severity": "medium",
                                "latency_ms": round(latency_ms, 1),
                                "message": (
                                    f"Pipeline '{pipeline_id}' response latency "
                                    f"{latency_ms:.0f}ms exceeds {latency_threshold}ms threshold"
                                ),
                                "timestamp": datetime.now(timezone.utc).isoformat(),
                            })
                except Exception as exc:
                    alerts.append({
                        "type": "pipeline_unreachable",
                        "pipeline_id": pipeline_id,
                        "severity": "high",
                        "message": f"Pipeline '{pipeline_id}' metrics unreachable: {exc}",
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    })

        return alerts

    # ── Record in evolution engine ─────────────────────────────────

    @staticmethod
    async def _record_alerts(alerts: List[Dict[str, Any]], ctx: AgentContext) -> None:
        try:
            from evolution.engine import get_evolution_engine
            engine = get_evolution_engine()
            for alert in alerts:
                await engine.record_feedback(
                    session_id=ctx.session_id or "monitor",
                    agent_name="MonitorAgent",
                    task_type=alert.get("type", "alert"),
                    user_prompt=alert.get("message", ""),
                    agent_output=alert,
                    success=False,  # alert means something went wrong
                    duration_ms=0.0,
                )
        except Exception as exc:
            logger.debug("Could not record alert in evolution engine: %s", exc)
