"""
Sprint S31b — Code generation service tests.

Tier A (pure Python, no optional deps).

Covers:
  * PlanStep schema validation
  * CodeGenerationEngine._build_prompt
  * CodeGenerationEngine._fallback (top products, trend, region, default)
  * CodeGenerationEngine.generate with mocked LLM
"""
from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.models import PlanStep

# ── PlanStep schema tests ─────────────────────────────────────────

class TestPlanStep:
    def test_minimal(self):
        step = PlanStep(step="Show revenue by month")
        assert step.step == "Show revenue by month"
        assert step.task is None
        assert step.chart_type is None

    def test_full(self):
        step = PlanStep(
            step="Top products by revenue",
            task="Group by product_name, sum total_revenue",
            chart_type="bar_chart",
        )
        assert step.chart_type == "bar_chart"


# ── CodeGenerationEngine tests ────────────────────────────────────

class TestBuildPrompt:
    def test_basic_prompt(self):
        from code_generation_service.main import CodeGenerationEngine
        step = PlanStep(step="Show total revenue")
        parts = CodeGenerationEngine._build_prompt(step)
        assert any("SQL" in p for p in parts)
        assert any("Show total revenue" in p for p in parts)

    def test_prompt_includes_task(self):
        from code_generation_service.main import CodeGenerationEngine
        step = PlanStep(step="Revenue query", task="SUM revenue grouped by month")
        parts = CodeGenerationEngine._build_prompt(step)
        assert any("SUM revenue" in p for p in parts)

    def test_prompt_includes_chart_type(self):
        from code_generation_service.main import CodeGenerationEngine
        step = PlanStep(step="Show data", chart_type="bar_chart")
        parts = CodeGenerationEngine._build_prompt(step)
        assert any("bar_chart" in p for p in parts)


class TestFallback:
    def test_default_fallback(self):
        from code_generation_service.main import CodeGenerationEngine
        step = PlanStep(step="Something generic")
        result = CodeGenerationEngine._fallback(step)
        assert "SELECT" in result["sql"]
        assert result["source"] == "fallback"

    def test_top_products_pattern(self):
        from code_generation_service.main import CodeGenerationEngine
        step = PlanStep(step="Show top products by revenue")
        result = CodeGenerationEngine._fallback(step)
        assert "product_name" in result["sql"]
        assert "GROUP BY" in result["sql"]
        assert "DESC" in result["sql"]

    def test_trend_pattern(self):
        from code_generation_service.main import CodeGenerationEngine
        step = PlanStep(step="Revenue trend over time")
        result = CodeGenerationEngine._fallback(step)
        assert "DATE_TRUNC" in result["sql"]
        assert "month" in result["sql"].lower()

    def test_region_pattern(self):
        from code_generation_service.main import CodeGenerationEngine
        step = PlanStep(step="Revenue by region")
        result = CodeGenerationEngine._fallback(step)
        assert "region" in result["sql"].lower()
        assert "GROUP BY" in result["sql"]

    def test_chart_type_preserved(self):
        from code_generation_service.main import CodeGenerationEngine
        step = PlanStep(step="Show top products", chart_type="pie")
        result = CodeGenerationEngine._fallback(step)
        assert result["visualization_suggestion"] == "pie"


class TestGenerate:
    def test_generate_uses_llm_when_available(self):
        from code_generation_service.main import CodeGenerationEngine
        engine = CodeGenerationEngine.__new__(CodeGenerationEngine)
        mock_llm = MagicMock()
        mock_llm.is_available.return_value = True
        mock_llm.generate.return_value = "SELECT SUM(revenue) FROM sales"
        engine._llm = mock_llm

        step = PlanStep(step="Total revenue")
        result = engine.generate(step)
        assert result["source"] == "llm"
        assert "SUM(revenue)" in result["sql"]

    def test_generate_strips_code_fences(self):
        from code_generation_service.main import CodeGenerationEngine
        engine = CodeGenerationEngine.__new__(CodeGenerationEngine)
        mock_llm = MagicMock()
        mock_llm.is_available.return_value = True
        mock_llm.generate.return_value = "```sql\nSELECT 1\n```"
        engine._llm = mock_llm

        step = PlanStep(step="Test")
        result = engine.generate(step)
        assert "```" not in result["sql"]
        assert result["sql"] == "SELECT 1"

    def test_generate_falls_back_when_llm_unavailable(self):
        from code_generation_service.main import CodeGenerationEngine
        engine = CodeGenerationEngine.__new__(CodeGenerationEngine)
        mock_llm = MagicMock()
        mock_llm.is_available.return_value = False
        engine._llm = mock_llm

        step = PlanStep(step="Something")
        result = engine.generate(step)
        assert result["source"] == "fallback"

    def test_generate_falls_back_on_empty_llm_response(self):
        from code_generation_service.main import CodeGenerationEngine
        engine = CodeGenerationEngine.__new__(CodeGenerationEngine)
        mock_llm = MagicMock()
        mock_llm.is_available.return_value = True
        mock_llm.generate.return_value = ""
        engine._llm = mock_llm

        step = PlanStep(step="Something")
        result = engine.generate(step)
        assert result["source"] == "fallback"
