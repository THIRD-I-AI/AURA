from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from typing import Optional, Protocol

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from shared.models import ChatRequest, AgentResponse, ValidationResult


@dataclass
class TinyRecursiveConfig:
    max_depth: int = 3
    confidence_threshold: float = 0.8


class GeneratorAgentProtocol(Protocol):
    def run(self, prompt: str, context: str, rework_feedback: str) -> str:
        ...

    def fallback(self, prompt: str, context: str) -> str:
        ...


class CriticAgentProtocol(Protocol):
    def run(self, original_prompt: str, generated_sql: str) -> ValidationResult:
        ...


class TinyRecursiveCoordinator:
    """Coordinates generator and critic agents using the Tiny-Recursive pattern."""

    def __init__(
        self,
        generator_agent: GeneratorAgentProtocol,
        critic_agent: CriticAgentProtocol,
        config: Optional[TinyRecursiveConfig] = None,
    ) -> None:
        self._generator = generator_agent
        self._critic = critic_agent
        self._config = config or TinyRecursiveConfig()

    def execute(self, request: ChatRequest) -> AgentResponse:
        attempt = 0
        rework_feedback: Optional[str] = None
        last_reason: Optional[str] = None

        while attempt < self._config.max_depth:
            generated_sql = self._generator.run(request.prompt, request.context or "", rework_feedback or "")
            validation_result = self._critic.run(request.prompt, generated_sql)

            if validation_result.is_valid:
                confidence = self._confidence_from_reason(validation_result.reason)
                if confidence >= self._config.confidence_threshold:
                    return AgentResponse(
                        status="Success",
                        final_query=generated_sql,
                        details=validation_result.reason,
                        confidence=confidence,
                        job_id=self._build_job_id(request.session_id, attempt),
                    )

            rework_feedback = validation_result.rework_suggestion or "Please refine the query for correctness."
            last_reason = validation_result.reason
            attempt += 1

        fallback_sql = self._generator.fallback(request.prompt, request.context or "")
        return AgentResponse(
            status="Fallback",
            final_query=fallback_sql,
            details=last_reason or "Returned fallback query after exhausting recursion depth.",
            confidence=0.3,
            job_id=self._build_job_id(request.session_id, attempt, fallback=True),
        )

    @staticmethod
    def _confidence_from_reason(reason: str) -> float:
        reason_lower = reason.lower()
        if "high" in reason_lower and "confidence" in reason_lower:
            return 0.9
        if "medium" in reason_lower:
            return 0.7
        if "low" in reason_lower:
            return 0.4
        return 0.6

    @staticmethod
    def _build_job_id(session_id: str, attempt: int, fallback: bool = False) -> str:
        suffix = "fallback" if fallback else f"attempt{attempt + 1}"
        return f"job_{session_id}_{suffix}"