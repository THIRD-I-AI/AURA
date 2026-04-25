"""
UASR Diagnostic Reflector Agent
=================================
Specialized agent that analyzes drift events and error logs to
hypothesize root causes. Part of the Controller-Reflector-Actuator loop.

Given a drift vector D⃗ and associated metadata, the Reflector:
  1. Classifies the drift category (schema, statistical, semantic)
  2. Analyzes affected columns and severity
  3. Produces a structured diagnosis with root-cause hypothesis
  4. Suggests a concrete recovery action for the Actuator
"""
from __future__ import annotations

import json
import logging
import os
import sys
from typing import Any, Dict, Optional, cast

from agents.base import AgentContext, AgentResult, AgentStatus, BaseAgent, Severity
from agents.params import ReflectorAgentParams
from uasr.models import DiagnosisResult, DriftDetectionResult, DriftType

logger = logging.getLogger("uasr.reflector")


class DiagnosticReflectorAgent(BaseAgent):
    """
    Analyzes a drift event and produces a structured diagnosis.

    Operates in two modes:
      - Rule-based (instant, no LLM) — handles common patterns
      - LLM-assisted — for complex or ambiguous drift vectors
    """

    name = "DiagnosticReflectorAgent"
    description = "Analyzes drift vectors and error logs to diagnose root causes."

    async def _run(self, ctx: AgentContext, result: AgentResult) -> AgentResult:
        await self._report("Analyzing drift event…", 10)

        params = cast(ReflectorAgentParams, ctx.metadata or {})
        drift_data = params.get("drift_result", {})
        error_logs = params.get("error_logs", [])

        if isinstance(drift_data, dict):
            drift = DriftDetectionResult(**drift_data)
        elif isinstance(drift_data, DriftDetectionResult):
            drift = drift_data
        else:
            result.status = AgentStatus.FAILED
            result.error = "No drift_result provided in metadata"
            return result

        await self._report(f"Drift type: {drift.drift_type}, severity: {drift.severity}", 30)

        # Try rule-based diagnosis first
        diagnosis = self._rule_based_diagnosis(drift, error_logs)

        if diagnosis.confidence < 0.6:
            # Attempt LLM-assisted diagnosis for higher confidence
            await self._report("Using LLM for deeper analysis…", 50)
            llm_diagnosis = await self._llm_diagnosis(drift, error_logs)
            if llm_diagnosis and llm_diagnosis.confidence > diagnosis.confidence:
                diagnosis = llm_diagnosis

        await self._report(f"Diagnosis complete: {diagnosis.root_cause}", 90)

        result.output = {
            "diagnosis": diagnosis.model_dump(),
            "drift_type": drift.drift_type.value if drift.drift_type else "unknown",
            "severity": drift.severity.value if drift.severity else "unknown",
        }
        result.artifacts["diagnosis"] = diagnosis

        result.add_step(
            action="diagnose_drift",
            tool_name="rule_engine",
            input_summary=f"drift_type={drift.drift_type}, columns={drift.affected_columns}",
            output_summary=f"root_cause={diagnosis.root_cause}, confidence={diagnosis.confidence}",
            severity=Severity.INFO,
        )

        return result

    # ────────────────────────────────────────────────────────────────
    # Rule-based diagnosis
    # ────────────────────────────────────────────────────────────────

    def _rule_based_diagnosis(
        self,
        drift: DriftDetectionResult,
        error_logs: list,
    ) -> DiagnosisResult:
        """Pattern-match common drift scenarios without an LLM."""

        diagnosis = DiagnosisResult(drift_event_id=drift.batch_id)

        if drift.drift_type == DriftType.SCHEMA:
            return self._diagnose_schema_drift(drift, diagnosis)
        elif drift.drift_type == DriftType.STATISTICAL:
            return self._diagnose_statistical_drift(drift, diagnosis)
        elif drift.drift_type == DriftType.SEMANTIC:
            return self._diagnose_semantic_drift(drift, diagnosis)
        else:
            diagnosis.root_cause = "Unknown drift type"
            diagnosis.hypothesis = "Could not classify the drift event"
            diagnosis.suggested_action = "Manual investigation required"
            diagnosis.confidence = 0.2
            return diagnosis

    def _diagnose_schema_drift(
        self, drift: DriftDetectionResult, diagnosis: DiagnosisResult
    ) -> DiagnosisResult:
        dv = drift.drift_vector
        change_type = dv.get("type", "schema_change")

        if change_type == "type_change":
            old_types = dv.get("old_types", {})
            new_types = dv.get("new_types", {})
            diagnosis.root_cause = "Column type change detected in upstream source"
            diagnosis.hypothesis = (
                f"Columns changed types: "
                f"{', '.join(f'{c}: {old_types.get(c)}->{new_types.get(c)}' for c in drift.affected_columns)}"
            )
            diagnosis.suggested_action = (
                "Generate a CAST shim to convert new types back to expected types, "
                "or update the pipeline schema to accept new types."
            )
            diagnosis.confidence = 0.85
        else:
            added = dv.get("added", [])
            removed = dv.get("removed", [])

            if removed and not added:
                diagnosis.root_cause = "Columns removed from upstream source"
                diagnosis.hypothesis = (
                    f"Upstream schema dropped columns: {removed}. "
                    "Likely a migration or API version change."
                )
                diagnosis.suggested_action = (
                    "Generate a shim that provides default/NULL values for removed columns "
                    "to maintain downstream compatibility."
                )
                diagnosis.confidence = 0.9
            elif added and not removed:
                diagnosis.root_cause = "New columns added to upstream source"
                diagnosis.hypothesis = (
                    f"Upstream added columns: {added}. "
                    "Schema evolved but existing pipeline not updated."
                )
                diagnosis.suggested_action = (
                    "Generate a column-filter shim to pass only expected columns, "
                    "and register new columns for future use."
                )
                diagnosis.confidence = 0.85
            else:
                diagnosis.root_cause = "Columns both added and removed — possible rename"
                diagnosis.hypothesis = (
                    f"Added: {added}, Removed: {removed}. "
                    "Likely a column rename in upstream migration."
                )
                diagnosis.suggested_action = (
                    "Generate a rename-mapping shim: map new column names to old names "
                    "for backward compatibility."
                )
                diagnosis.confidence = 0.75

        return diagnosis

    def _diagnose_statistical_drift(
        self, drift: DriftDetectionResult, diagnosis: DiagnosisResult
    ) -> DiagnosisResult:
        kl = drift.kl_divergence or 0.0
        dv = drift.drift_vector
        max_kl = dv.get("max_kl", kl)
        zeta = dv.get("threshold_zeta", 0.15)

        if max_kl > zeta * 5:
            diagnosis.root_cause = "Severe distribution shift — possible data corruption or source switchover"
            diagnosis.hypothesis = (
                f"KL divergence ({max_kl:.4f}) is >5x threshold ({zeta:.4f}). "
                "Data distribution has fundamentally changed."
            )
            diagnosis.suggested_action = (
                "Generate a normalization shim that clips outliers and re-scales values "
                "to match the baseline distribution. Flag for human review."
            )
            diagnosis.confidence = 0.7
        elif max_kl > zeta * 2:
            diagnosis.root_cause = "Significant distribution drift — seasonal or upstream change"
            diagnosis.hypothesis = (
                f"KL divergence ({max_kl:.4f}) indicates meaningful shift in columns: "
                f"{drift.affected_columns}"
            )
            diagnosis.suggested_action = (
                "Generate a winsorization shim to cap extreme values, "
                "then update the baseline if drift is intentional."
            )
            diagnosis.confidence = 0.8
        else:
            diagnosis.root_cause = "Mild statistical drift"
            diagnosis.hypothesis = (
                f"KL divergence ({max_kl:.4f}) slightly above threshold ({zeta:.4f}). "
                "May be natural variation."
            )
            diagnosis.suggested_action = (
                "Generate a monitoring-only shim that logs drift metrics. "
                "Consider updating the baseline."
            )
            diagnosis.confidence = 0.85

        return diagnosis

    def _diagnose_semantic_drift(
        self, drift: DriftDetectionResult, diagnosis: DiagnosisResult
    ) -> DiagnosisResult:
        cos_dist = drift.cosine_distance or 0.0

        diagnosis.root_cause = "Semantic meaning of data has shifted"
        diagnosis.hypothesis = (
            f"Batch embedding cosine distance ({cos_dist:.4f}) exceeds semantic threshold. "
            "The nature of the data has changed (e.g., different product categories, "
            "new entity types, or language shift)."
        )
        diagnosis.suggested_action = (
            "Generate a semantic mapping shim that re-encodes the batch against "
            "the reference ontology, or update the reference context matrix."
        )
        diagnosis.confidence = 0.65
        return diagnosis

    # ────────────────────────────────────────────────────────────────
    # LLM-assisted diagnosis (fallback)
    # ────────────────────────────────────────────────────────────────

    async def _llm_diagnosis(
        self,
        drift: DriftDetectionResult,
        error_logs: list,
    ) -> Optional[DiagnosisResult]:
        """Use LLM to analyze complex drift patterns."""
        try:
            from shared.llm_provider import get_llm

            llm = get_llm()
            if not llm or not llm.is_available():
                return None

            prompt = [
                "You are a data pipeline diagnostician. Analyze this drift event and provide a diagnosis.",
                json.dumps({
                    "drift_type": drift.drift_type.value if drift.drift_type else "unknown",
                    "severity": drift.severity.value if drift.severity else "unknown",
                    "kl_divergence": drift.kl_divergence,
                    "cosine_distance": drift.cosine_distance,
                    "affected_columns": drift.affected_columns,
                    "drift_vector": drift.drift_vector,
                    "error_logs": error_logs[:5],
                }),
                (
                    "Respond ONLY with JSON: "
                    '{"root_cause": "...", "hypothesis": "...", "suggested_action": "...", "confidence": 0.XX}'
                ),
            ]

            resp = llm.generate_json(prompt)
            if resp and "root_cause" in resp:
                return DiagnosisResult(
                    drift_event_id=drift.batch_id,
                    root_cause=resp["root_cause"],
                    hypothesis=resp.get("hypothesis", ""),
                    suggested_action=resp.get("suggested_action", ""),
                    confidence=min(float(resp.get("confidence", 0.5)), 0.95),
                )
        except Exception as exc:
            logger.warning("LLM diagnosis failed: %s", exc)

        return None
