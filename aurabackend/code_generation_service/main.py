from __future__ import annotations

import os
import sys
from typing import Any, Dict

from fastapi import HTTPException

from shared.llm_provider import get_llm
from shared.logging_config import get_logger
from shared.models import PlanStep

# Add parent directory to path
from shared.service_factory import create_service

logger = get_logger("aura.code_generation")

code_gen_app = create_service(
    name="Code Generation",
    service_tag="code_generation",
)


class CodeGenerationEngine:
    def __init__(self) -> None:
        self._llm = get_llm(model=os.getenv("CODEGEN_MODEL", os.getenv("GENERATOR_MODEL", "")))

    @staticmethod
    def _build_prompt(step: PlanStep) -> list[str]:
        instructions = (
            "You are AURA's analytics assistant. Generate a valid PostgreSQL SQL query "
            "for the described plan step. Include only SQL in the response body. "
            "Always enclose ALL table and column names in double quotes "
            '(e.g., "my_table"."my_column") to ensure compatibility with identifiers '
            "containing special characters like '&', spaces, or reserved keywords."
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

    def generate(self, step: PlanStep) -> Dict[str, Any]:
        """Generate SQL via LLM, or raise HTTPException 503 with a clear
        actionable reason. Previously this returned templated SQL
        referencing a fictional ``sales_table`` whose product_name,
        total_revenue, region columns don't exist for any real user —
        producing a query that the execution engine then failed on,
        leaving the user staring at a "table doesn't exist" error
        chain and no idea that the root cause was a missing/invalid
        LLM key. Failing loudly here surfaces the real reason.
        """
        prompt = self._build_prompt(step)
        if not self._llm.is_available():
            raise HTTPException(
                status_code=503,
                detail=(
                    "No LLM provider configured. Set one of "
                    "GROQ_API_KEY, GEMINI_API_KEY, OPENAI_API_KEY in the "
                    "service's environment, or point OLLAMA_HOST at a "
                    "running Ollama instance."
                ),
            )
        try:
            raw = self._llm.generate(prompt)
        except Exception as exc:
            logger.warning("CodeGenerationEngine LLM call failed: %s", exc)
            raise HTTPException(
                status_code=502,
                detail=f"LLM call failed: {exc}",
            ) from exc

        if not raw or not raw.strip():
            raise HTTPException(
                status_code=502,
                detail="LLM returned an empty SQL response.",
            )

        sql = raw.strip().replace("```sql", "").replace("```", "").strip()
        if not sql:
            raise HTTPException(
                status_code=502,
                detail="LLM response was only markdown fences with no SQL.",
            )
        return {
            "sql": sql,
            "visualization_suggestion": step.chart_type or "table",
            "source": "llm",
        }


_engine = CodeGenerationEngine()


# Health is provided by create_service()


@code_gen_app.post("/generate_code")
async def generate_code(step: PlanStep) -> Dict[str, Any]:
    if not step.step:
        raise HTTPException(status_code=400, detail="plan step description is required")
    result = _engine.generate(step)
    return result
