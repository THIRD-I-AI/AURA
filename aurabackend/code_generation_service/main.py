from __future__ import annotations

import os
import sys
from typing import Any, Dict

import google.generativeai as genai
from fastapi import FastAPI, HTTPException

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.models import PlanStep
from shared.secret_resolver import secret_resolver


code_gen_app = FastAPI(title="AURA Code Generation Service")


class CodeGenerationEngine:
	def __init__(self) -> None:
		self._model_name = os.getenv("CODEGEN_MODEL", os.getenv("GENERATOR_MODEL", "gemini-2.5-flash"))
		self._api_key = secret_resolver.get_secret("GEMINI_API_KEY") or secret_resolver.get_secret("GOOGLE_API_KEY")
		self._model: Any | None = None
		if self._api_key:
			try:
				configure_fn = getattr(genai, "configure", None)
				if callable(configure_fn):
					configure_fn(api_key=self._api_key)
				model_cls = getattr(genai, "GenerativeModel", None)
				if model_cls:
					self._model = model_cls(self._model_name)
			except Exception as exc:  # pragma: no cover - defensive log path
				print(f"CodeGenerationEngine: failed to initialize Gemini model - {exc}")

	@staticmethod
	def _build_prompt(step: PlanStep) -> list[str]:
		instructions = (
			"You are AURA's analytics assistant. Generate a valid PostgreSQL SQL query "
			"for the described plan step. Include only SQL in the response body."
		)
		context_bits = [instructions]
		context_bits.append(f"Plan step: {step.step}")
		if step.task:
			context_bits.append(f"Task details: {step.task}")
		if step.chart_type:
			context_bits.append(
				"Preferred visualisation: "
				f"{step.chart_type}. Select columns that suit this chart."
			)
		context_bits.append(
			"Respond with ONLY the SQL statement. Do not add explanations or code fences."
		)
		return context_bits

	@staticmethod
	def _fallback(step: PlanStep) -> Dict[str, Any]:
		step_lower = f"{step.step} {step.task or ''}".lower()
		sql = "SELECT * FROM sales_table LIMIT 100;"
		chart = step.chart_type or "table"

		if "top" in step_lower and "product" in step_lower:
			sql = (
				"SELECT product_name, SUM(total_revenue) AS total_revenue "
				"FROM sales_table GROUP BY product_name ORDER BY total_revenue DESC LIMIT 10;"
			)
			chart = chart or "bar_chart"
		elif "trend" in step_lower or "over time" in step_lower:
			sql = (
				"SELECT DATE_TRUNC('month', sale_date) AS month, SUM(total_revenue) AS monthly_revenue "
				"FROM sales_table GROUP BY month ORDER BY month;"
			)
			chart = chart or "line_chart"
		elif "region" in step_lower:
			sql = (
				"SELECT region, SUM(total_revenue) AS regional_revenue "
				"FROM sales_table GROUP BY region ORDER BY regional_revenue DESC;"
			)
			chart = chart or "choropleth"

		return {
			"sql": sql,
			"visualization_suggestion": chart,
			"source": "fallback",
		}

	def generate(self, step: PlanStep) -> Dict[str, Any]:
		prompt = self._build_prompt(step)
		if self._model:
			try:
				response = self._model.generate_content(prompt)
				sql = (response.text or "").strip().replace("```sql", "").replace("```", "").strip()
				if sql:
					chart = step.chart_type or "table"
					return {
						"sql": sql,
						"visualization_suggestion": chart,
						"source": "gemini",
					}
			except Exception as exc:  # pragma: no cover - remote failure path
				print(f"CodeGenerationEngine: remote generation failed - {exc}")

		return self._fallback(step)


_engine = CodeGenerationEngine()


@code_gen_app.get("/health")
async def health() -> Dict[str, str]:
	return {"status": "healthy", "service": "code_generation"}


@code_gen_app.post("/generate_code")
async def generate_code(step: PlanStep) -> Dict[str, Any]:
	if not step.step:
		raise HTTPException(status_code=400, detail="plan step description is required")
	result = _engine.generate(step)
	return result
