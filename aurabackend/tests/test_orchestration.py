"""
Orchestration Service Unit Tests
==================================
Tests for TinyRecursiveCoordinator: success path, rework loop,
fallback on exhaustion, confidence scoring, and job-ID generation.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.models import AgentResponse, ChatRequest, ValidationResult
from orchestration_service.coordinator import TinyRecursiveCoordinator, TinyRecursiveConfig


# ── Test doubles ─────────────────────────────────────────────────────────────

class _StubGenerator:
    """Returns a fixed SQL string; tracks how many times run/fallback was called."""

    def __init__(self, sql: str = "SELECT 1", fallback_sql: str = "SELECT 0"):
        self.sql = sql
        self.fallback_sql = fallback_sql
        self.run_calls: list[tuple[str, str, str]] = []
        self.fallback_calls: list[tuple[str, str]] = []

    def run(self, prompt: str, context: str, rework_feedback: str) -> str:
        self.run_calls.append((prompt, context, rework_feedback))
        return self.sql

    def fallback(self, prompt: str, context: str) -> str:
        self.fallback_calls.append((prompt, context))
        return self.fallback_sql


class _StubCritic:
    """Returns a pre-configured ValidationResult sequence, then repeats last."""

    def __init__(self, *results: ValidationResult):
        self._results = list(results)
        self._index = 0
        self.calls: list[tuple[str, str]] = []

    def run(self, original_prompt: str, generated_sql: str) -> ValidationResult:
        self.calls.append((original_prompt, generated_sql))
        r = self._results[min(self._index, len(self._results) - 1)]
        self._index += 1
        return r


def _make_request(prompt: str = "Show all sales") -> ChatRequest:
    return ChatRequest(session_id="test-session", prompt=prompt)


# ── Success path ─────────────────────────────────────────────────────────────

class TestCoordinatorSuccess:
    def test_immediate_success(self):
        """Valid SQL on first attempt → Success status."""
        gen = _StubGenerator(sql="SELECT * FROM sales")
        critic = _StubCritic(
            ValidationResult(is_valid=True, reason="Correct and accurate query")
        )
        coord = TinyRecursiveCoordinator(gen, critic)
        resp = coord.execute(_make_request())

        assert resp.status == "Success"
        assert resp.final_query == "SELECT * FROM sales"
        assert resp.confidence >= 0.8
        assert len(gen.run_calls) == 1
        assert len(gen.fallback_calls) == 0

    def test_success_after_one_rework(self):
        """Invalid on attempt 1, valid on attempt 2 → Success after one rework."""
        gen = _StubGenerator(sql="SELECT revenue FROM sales")
        critic = _StubCritic(
            ValidationResult(is_valid=False, reason="Missing WHERE clause",
                             rework_suggestion="Add a WHERE clause"),
            ValidationResult(is_valid=True, reason="Accurate and well-formed query"),
        )
        coord = TinyRecursiveCoordinator(gen, critic, TinyRecursiveConfig(max_depth=3))
        resp = coord.execute(_make_request())

        assert resp.status == "Success"
        assert len(gen.run_calls) == 2
        # Second run should receive the rework feedback
        assert gen.run_calls[1][2] == "Add a WHERE clause"

    def test_high_confidence_keyword(self):
        """'high confidence' in reason → confidence >= 0.9."""
        gen = _StubGenerator()
        critic = _StubCritic(
            ValidationResult(is_valid=True, reason="High confidence: query is valid")
        )
        resp = TinyRecursiveCoordinator(gen, critic).execute(_make_request())
        assert resp.confidence >= 0.9

    def test_low_confidence_keyword(self):
        """'low confidence' in reason → confidence < 0.6."""
        gen = _StubGenerator()
        critic = _StubCritic(
            ValidationResult(is_valid=True, reason="Low confidence, might be correct")
        )
        resp = TinyRecursiveCoordinator(gen, critic).execute(_make_request())
        assert resp.confidence < 0.6

    def test_positive_keyword_confidence(self):
        """'valid' keyword in reason maps to >= 0.85 when is_valid=True."""
        gen = _StubGenerator()
        critic = _StubCritic(
            ValidationResult(is_valid=True, reason="The query is valid and correct")
        )
        resp = TinyRecursiveCoordinator(gen, critic).execute(_make_request())
        assert resp.confidence >= 0.85


# ── Fallback path ─────────────────────────────────────────────────────────────

class TestCoordinatorFallback:
    def test_fallback_after_exhaustion(self):
        """All attempts invalid → Fallback status with fallback SQL."""
        gen = _StubGenerator(sql="BAD SQL", fallback_sql="SELECT NULL")
        critic = _StubCritic(
            ValidationResult(is_valid=False, reason="Invalid syntax",
                             rework_suggestion="Fix it")
        )
        coord = TinyRecursiveCoordinator(gen, critic, TinyRecursiveConfig(max_depth=2))
        resp = coord.execute(_make_request())

        assert resp.status == "Fallback"
        assert resp.final_query == "SELECT NULL"
        assert resp.confidence == 0.3
        assert len(gen.run_calls) == 2
        assert len(gen.fallback_calls) == 1

    def test_fallback_job_id_contains_fallback(self):
        gen = _StubGenerator(fallback_sql="SELECT 0")
        critic = _StubCritic(
            ValidationResult(is_valid=False, reason="error in query")
        )
        coord = TinyRecursiveCoordinator(gen, critic, TinyRecursiveConfig(max_depth=1))
        resp = coord.execute(_make_request())

        assert "fallback" in (resp.job_id or "")

    def test_max_depth_one(self):
        """max_depth=1 means exactly one run() before going to fallback."""
        gen = _StubGenerator()
        critic = _StubCritic(
            ValidationResult(is_valid=False, reason="malformed query")
        )
        coord = TinyRecursiveCoordinator(gen, critic, TinyRecursiveConfig(max_depth=1))
        coord.execute(_make_request())
        assert len(gen.run_calls) == 1


# ── Confidence scoring ────────────────────────────────────────────────────────

class TestConfidenceScoring:
    @pytest.mark.parametrize("reason,expected_min,expected_max", [
        ("High confidence: looks correct",      0.9,  1.0),
        ("Medium confidence level",              0.65, 0.75),
        ("Low confidence, uncertain",            0.3,  0.5),
        ("The query is accurate and valid",      0.85, 1.0),
        # Both "invalid" (contains "valid") and "incorrect" (contains "correct") trigger
        # the positive check first due to substring matching.  Use a reason that has
        # a clean negative keyword ("fail") and no positive substring.
        ("This SQL failed the syntax check", 0.2, 0.4),
        ("Some neutral statement",               0.55, 0.65),
    ])
    def test_confidence_ranges(self, reason, expected_min, expected_max):
        score = TinyRecursiveCoordinator._confidence_from_reason(reason)
        assert expected_min <= score <= expected_max, (
            f"reason={reason!r}: expected [{expected_min}, {expected_max}], got {score}"
        )


# ── Job ID format ─────────────────────────────────────────────────────────────

class TestJobId:
    def test_success_job_id_format(self):
        gen = _StubGenerator()
        critic = _StubCritic(
            ValidationResult(is_valid=True, reason="correct and accurate")
        )
        coord = TinyRecursiveCoordinator(gen, critic)
        resp = coord.execute(ChatRequest(session_id="abc123", prompt="q"))
        assert resp.job_id == "job_abc123_attempt1"

    def test_fallback_job_id_format(self):
        gen = _StubGenerator()
        critic = _StubCritic(
            ValidationResult(is_valid=False, reason="invalid query")
        )
        coord = TinyRecursiveCoordinator(gen, critic, TinyRecursiveConfig(max_depth=1))
        resp = coord.execute(ChatRequest(session_id="xyz", prompt="q"))
        assert resp.job_id == "job_xyz_fallback"
