"""
UASR Recovery Loop — Controller-Reflector-Actuator
=====================================================
Orchestrates the full self-healing cycle:

  1. Controller receives a drift detection result
  2. Reflector (DiagnosticReflectorAgent) diagnoses root cause
  3. Actuator (SynthesisActuatorAgent) generates a JIT shim
  4. Sandbox validates the shim against the drifted batch
  5. If validation passes (D_KL reduced to nominal), deploy to production stream

The loop supports configurable max iterations and automatic rollback.
"""
from __future__ import annotations

import logging
import os
import sys
import time
import uuid
from typing import Any, Callable, Dict, List, Optional

from agents.base import AgentContext, AgentStatus

from .actuator_agent import SynthesisActuatorAgent
from .drift_detector import DriftDetector
from .models import (
    BatchPayload,
    DriftDetectionResult,
    DriftType,
    RecoveryLoopResult,
    RecoveryStatus,
    ShimResult,
)
from .reflector_agent import DiagnosticReflectorAgent

logger = logging.getLogger("uasr.recovery_loop")


class RecoveryLoopConfig:
    """Configuration for the Controller-Reflector-Actuator loop."""

    def __init__(
        self,
        max_iterations: int = 3,
        kl_reduction_target: float = 0.5,
        sandbox_timeout_seconds: float = 30.0,
        auto_deploy: bool = True,
        # S18.1b: opt-in causal-RL shim selection. When True, the loop
        # collects all validated candidates across iterations and then
        # runs the CausalRLEvaluator to pick the winner by
        # counterfactual expected-improvement, rather than deploying
        # the first validated shim greedily.
        use_causal_rl_evaluator: bool = False,
    ) -> None:
        self.max_iterations = max_iterations
        self.kl_reduction_target = kl_reduction_target
        self.sandbox_timeout_seconds = sandbox_timeout_seconds
        self.auto_deploy = auto_deploy
        self.use_causal_rl_evaluator = use_causal_rl_evaluator


class RecoveryLoop:
    """
    Orchestrates the Controller-Reflector-Actuator pattern.

    Usage:
        loop = RecoveryLoop(detector, config)
        result = await loop.run(drift_result, original_batch)
    """

    def __init__(
        self,
        detector: DriftDetector,
        config: Optional[RecoveryLoopConfig] = None,
        on_shim_deployed: Optional[Callable] = None,
    ) -> None:
        self._detector = detector
        self._config = config or RecoveryLoopConfig()
        self._reflector = DiagnosticReflectorAgent()
        self._actuator = SynthesisActuatorAgent()
        self._on_shim_deployed = on_shim_deployed

        # Shim registry: source_id → [deployed shim codes]
        self._deployed_shims: Dict[str, List[str]] = {}

        # S18.1b: lazily construct the causal-RL evaluator only when
        # opted in. The drift_score_fn delegates to the detector's
        # KL divergence (or falls back to 1.0/0.0 binary for schema
        # drift where kl_divergence is None).
        self._evaluator = None
        if self._config.use_causal_rl_evaluator:
            from .causal_rl_evaluator import CausalRLEvaluator

            def _drift_score(rows: List[Dict[str, Any]]) -> float:
                batch = BatchPayload(
                    source_id="__eval__",
                    batch_id="__eval__",
                    rows=rows,
                )
                result = self._detector.detect(batch)
                if result.kl_divergence is not None:
                    return result.kl_divergence
                return 1.0 if result.drift_detected else 0.0

            self._evaluator = CausalRLEvaluator(drift_score_fn=_drift_score)

    # ────────────────────────────────────────────────────────────────
    # Main loop
    # ────────────────────────────────────────────────────────────────

    async def run(
        self,
        drift_result: DriftDetectionResult,
        original_batch: BatchPayload,
    ) -> RecoveryLoopResult:
        """Execute the full recovery cycle."""
        start_time = time.time()
        recovery_id = uuid.uuid4().hex[:16]

        loop_result = RecoveryLoopResult(
            drift_event_id=drift_result.batch_id,
            recovery_id=recovery_id,
            status=RecoveryStatus.DETECTED,
        )

        logger.info(
            "Recovery loop started: drift_event=%s, type=%s, severity=%s",
            drift_result.batch_id,
            drift_result.drift_type,
            drift_result.severity,
        )

        # S18.1b: when the evaluator is on, we collect all validated
        # candidates across iterations and defer deployment to the end.
        # When off, the greedy "first-validated wins" path is unchanged.
        validated_candidates: List[tuple] = []  # (shim, validation)

        for iteration in range(self._config.max_iterations):
            logger.info("Recovery iteration %d/%d", iteration + 1, self._config.max_iterations)

            # ── Step 1: Diagnose ────────────────────────────────────
            loop_result.status = RecoveryStatus.DIAGNOSING
            diagnosis = await self._diagnose(drift_result, iteration)

            if not diagnosis:
                loop_result.status = RecoveryStatus.FAILED
                logger.error("Diagnosis failed on iteration %d", iteration + 1)
                break

            loop_result.diagnosis = diagnosis

            # ── Step 2: Generate shim ───────────────────────────────
            loop_result.status = RecoveryStatus.GENERATING_SHIM
            shim = await self._generate_shim(drift_result, diagnosis, recovery_id)

            if not shim or not shim.shim_code:
                loop_result.status = RecoveryStatus.FAILED
                logger.error("Shim generation failed on iteration %d", iteration + 1)
                break

            # ── Step 3: Validate in sandbox ─────────────────────────
            loop_result.status = RecoveryStatus.VALIDATING
            validation = await self._validate_shim(shim, original_batch, drift_result)

            shim.validation_passed = validation["passed"]
            shim.post_kl_divergence = validation.get("post_kl")
            loop_result.shim = shim

            if validation["passed"]:
                if self._evaluator is not None:
                    # S18.1b path: collect the validated shim as a
                    # candidate and continue iterating to gather more.
                    validated_candidates.append((shim, validation))
                    logger.info(
                        "Candidate %d collected (post_kl=%s), continuing",
                        len(validated_candidates),
                        validation.get("post_kl"),
                    )
                    drift_result = self._update_drift_with_feedback(
                        drift_result, validation,
                    )
                    continue

                # ── Greedy path (original): deploy immediately ──────
                if self._config.auto_deploy:
                    self._deploy_shim(loop_result, shim, drift_result, recovery_id, validation)
                else:
                    loop_result.status = RecoveryStatus.VALIDATING
                    logger.info("Shim validated but auto_deploy=False, awaiting manual deploy")

                break
            else:
                logger.warning(
                    "Shim validation failed (iteration %d): %s",
                    iteration + 1,
                    validation.get("reason", "unknown"),
                )
                drift_result = self._update_drift_with_feedback(drift_result, validation)

        else:
            # Exhausted iterations
            if not validated_candidates:
                loop_result.status = RecoveryStatus.FAILED
                logger.error("Recovery loop exhausted %d iterations", self._config.max_iterations)

        # S18.1b: if we collected candidates, run the evaluator now
        if validated_candidates and self._evaluator is not None:
            winner_shim, winner_val = await self._select_winner_via_evaluator(
                validated_candidates, drift_result, original_batch, loop_result,
            )
            if winner_shim is not None and self._config.auto_deploy:
                self._deploy_shim(
                    loop_result, winner_shim, drift_result,
                    recovery_id, winner_val,
                )

        loop_result.total_latency_seconds = time.time() - start_time
        return loop_result

    # ────────────────────────────────────────────────────────────────
    # Internal steps
    # ────────────────────────────────────────────────────────────────

    async def _diagnose(self, drift: DriftDetectionResult, iteration: int):
        """Run the Reflector agent."""
        ctx = AgentContext(
            user_prompt="Diagnose data drift",
            task_description=f"Analyze drift event (iteration {iteration + 1})",
            metadata={
                "drift_result": drift.model_dump(),
                "iteration": iteration,
            },
        )

        result = await self._reflector.execute(ctx)
        if result.succeeded and "diagnosis" in result.artifacts:
            return result.artifacts["diagnosis"]

        logger.warning("Reflector failed: %s", result.error)
        return None

    async def _generate_shim(self, drift, diagnosis, recovery_id: str):
        """Run the Actuator agent."""
        ctx = AgentContext(
            user_prompt="Generate recovery shim",
            task_description="Create a JIT transformation to bridge data drift",
            metadata={
                "diagnosis": diagnosis.model_dump() if hasattr(diagnosis, "model_dump") else diagnosis,
                "drift_result": drift.model_dump(),
                "drift_type": drift.drift_type.value if drift.drift_type else "unknown",
                "drift_vector": drift.drift_vector,
                "recovery_id": recovery_id,
            },
        )

        result = await self._actuator.execute(ctx)
        if result.succeeded and "shim" in result.artifacts:
            return result.artifacts["shim"]

        logger.warning("Actuator failed: %s", result.error)
        return None

    async def _validate_shim(
        self,
        shim: ShimResult,
        original_batch: BatchPayload,
        drift: DriftDetectionResult,
    ) -> Dict[str, Any]:
        """
        Validate the shim by:
          1. Executing the transform function in a sandboxed environment
          2. Computing post-shim distributions
          3. Checking if D_KL is reduced to nominal levels
        """
        if not shim.shim_code or not original_batch.rows:
            return {"passed": False, "reason": "Empty shim or batch"}

        try:
            # Execute shim in restricted namespace
            transformed_rows = self._sandbox_execute(shim.shim_code, original_batch.rows)

            if not transformed_rows:
                return {"passed": False, "reason": "Shim produced empty output"}

            # Build a new batch from transformed data
            transformed_batch = BatchPayload(
                source_id=original_batch.source_id,
                batch_id=f"{original_batch.batch_id}_shimmed",
                columns=list(transformed_rows[0].keys()) if transformed_rows else [],
                rows=transformed_rows,
                schema_snapshot=original_batch.schema_snapshot,
            )

            # Re-run drift detection on transformed data
            post_drift = self._detector.detect(transformed_batch)

            if not post_drift.drift_detected:
                return {
                    "passed": True,
                    "post_kl": 0.0,
                    "reason": "Drift fully resolved",
                }

            # Check if KL divergence reduced sufficiently
            post_kl = post_drift.kl_divergence or 0.0
            pre_kl = drift.kl_divergence or 0.0

            if pre_kl > 0 and post_kl < pre_kl * self._config.kl_reduction_target:
                return {
                    "passed": True,
                    "post_kl": post_kl,
                    "reason": f"KL reduced from {pre_kl:.4f} to {post_kl:.4f}",
                }

            # For schema drift: check if schema is now clean
            if drift.drift_type == DriftType.SCHEMA and not post_drift.drift_detected:
                return {
                    "passed": True,
                    "post_kl": 0.0,
                    "reason": "Schema drift resolved",
                }

            return {
                "passed": False,
                "post_kl": post_kl,
                "reason": (
                    f"Insufficient improvement: pre_kl={pre_kl:.4f}, post_kl={post_kl:.4f}, "
                    f"target=<{pre_kl * self._config.kl_reduction_target:.4f}"
                ),
            }

        except Exception as exc:
            logger.error("Shim validation error: %s", exc)
            return {"passed": False, "reason": f"Execution error: {exc}"}

    @staticmethod
    def _sandbox_execute(shim_code: str, rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Execute a shim's transform() function in a restricted namespace.
        Only standard library modules are available.
        """
        allowed_globals = {
            "__builtins__": {
                "len": len, "min": min, "max": max, "abs": abs, "sum": sum,
                "int": int, "float": float, "str": str, "bool": bool,
                "list": list, "dict": dict, "set": set, "tuple": tuple,
                "range": range, "enumerate": enumerate, "zip": zip,
                "isinstance": isinstance, "type": type, "round": round,
                "None": None, "True": True, "False": False,
                "ValueError": ValueError, "TypeError": TypeError,
                "KeyError": KeyError, "Exception": Exception,
            },
        }

        # Allow logging import
        import logging as _logging
        allowed_globals["logging"] = _logging

        namespace: Dict[str, Any] = {}
        exec(shim_code, allowed_globals, namespace)  # noqa: S102

        transform_fn = namespace.get("transform")
        if not callable(transform_fn):
            raise ValueError("Shim does not define a callable `transform` function")

        # Deep copy rows to prevent mutation
        import copy
        safe_rows = copy.deepcopy(rows)

        result = transform_fn(safe_rows)

        if not isinstance(result, list):
            raise ValueError(f"transform() returned {type(result).__name__}, expected list")

        return result

    @staticmethod
    def _update_drift_with_feedback(
        drift: DriftDetectionResult,
        validation: Dict[str, Any],
    ) -> DriftDetectionResult:
        """Enrich the drift result with validation feedback for the next iteration."""
        drift.details += f" | Validation feedback: {validation.get('reason', 'N/A')}"
        if "post_kl" in validation:
            drift.drift_vector["prev_post_kl"] = validation["post_kl"]
        return drift

    # ────────────────────────────────────────────────────────────────
    # S18.1b: deployment helper + evaluator wiring
    # ────────────────────────────────────────────────────────────────

    def _deploy_shim(
        self,
        loop_result: RecoveryLoopResult,
        shim: ShimResult,
        drift_result: DriftDetectionResult,
        recovery_id: str,
        validation: Dict[str, Any],
    ) -> None:
        loop_result.status = RecoveryStatus.DEPLOYED
        loop_result.shim = shim
        shim.deployed = True
        self._deployed_shims.setdefault(
            drift_result.source_id, [],
        ).append(shim.shim_code)

        if self._on_shim_deployed:
            try:
                self._on_shim_deployed(
                    drift_result.source_id,
                    shim.shim_code,
                    recovery_id,
                )
            except Exception as exc:
                logger.warning("on_shim_deployed callback failed: %s", exc)

        logger.info(
            "Shim deployed: recovery=%s, post_kl=%s",
            recovery_id, validation.get("post_kl"),
        )

    async def _select_winner_via_evaluator(
        self,
        validated_candidates: List[tuple],
        drift_result: DriftDetectionResult,
        original_batch: BatchPayload,
        loop_result: RecoveryLoopResult,
    ) -> tuple:
        """S18.1b: run CausalRLEvaluator on the collected candidates
        and return (winner_shim, winner_validation) or (None, None).
        """
        from .causal_rl_evaluator import ShimCandidate

        candidates = []
        shim_map: Dict[str, tuple] = {}
        for i, (shim, val) in enumerate(validated_candidates):
            cid = f"candidate_{i}"
            code = shim.shim_code

            def _make_transform(c: str):
                def transform(source_id: str, rows: List[Dict[str, Any]]):
                    return self._sandbox_execute(c, rows)
                return transform

            candidates.append(ShimCandidate(
                candidate_id=cid,
                transform=_make_transform(code),
                source="recovery_loop",
                metadata={"post_kl": val.get("post_kl")},
            ))
            shim_map[cid] = (shim, val)

        try:
            artifact = await self._evaluator.select_winner(
                source_id=drift_result.source_id,
                drift_event=drift_result,
                batch=original_batch,
                candidates=candidates,
            )
            loop_result.evaluation_artifact = {
                "record_id": artifact.record_id,
                "winner_id": artifact.winner_id,
                "selection_rationale": artifact.selection_rationale,
                "candidates": [
                    {"id": c.candidate_id, "improvement": c.improvement}
                    for c in artifact.candidates
                ],
            }
            if artifact.winner_id and artifact.winner_id in shim_map:
                logger.info(
                    "Evaluator selected %s (%s)",
                    artifact.winner_id,
                    artifact.selection_rationale,
                )
                return shim_map[artifact.winner_id]
            logger.warning("Evaluator returned no winner")
        except Exception as exc:
            logger.warning("CausalRLEvaluator failed (non-fatal): %s", exc)

        # Fallback: use the first validated candidate (greedy)
        if validated_candidates:
            logger.info("Falling back to first validated candidate")
            return validated_candidates[0]
        return (None, None)

    # ────────────────────────────────────────────────────────────────
    # Shim management
    # ────────────────────────────────────────────────────────────────

    def get_deployed_shims(self, source_id: str) -> List[str]:
        """Return all deployed shim codes for a source."""
        return self._deployed_shims.get(source_id, [])

    def rollback_last_shim(self, source_id: str) -> bool:
        """Remove the most recently deployed shim for a source."""
        shims = self._deployed_shims.get(source_id, [])
        if shims:
            shims.pop()
            logger.info("Rolled back last shim for source=%s", source_id)
            return True
        return False

    def apply_shims(self, source_id: str, rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Apply all deployed shims for a source in order."""
        shims = self._deployed_shims.get(source_id, [])
        for shim_code in shims:
            try:
                rows = self._sandbox_execute(shim_code, rows)
            except Exception as exc:
                logger.error("Shim application failed for source=%s: %s", source_id, exc)
                break
        return rows
