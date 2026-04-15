"""
SQL Generator Agent
===================
Handles: Natural-language → SQL generation.  Wraps the existing AURA code-gen
service and adds schema-aware context injection + validation.
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Dict, List, Optional

from agents.base import AgentContext, AgentResult, BaseAgent, Severity
from shared.llm_provider import get_llm

logger = logging.getLogger("aura.agents.sql_generator")

_SQL_GEN_PROMPT = """\
You are an expert SQL engineer.  Given the schema and the user's natural-
language question, produce a single executable SQL query (PostgreSQL dialect).

SCHEMA:
{schema}

USER QUESTION:
{question}

RULES:
- Return ONLY the SQL statement.  No explanations, no markdown fences.
- Always enclose ALL table and column names in double quotes (e.g., "my_table"."my_column") to ensure compatibility with identifiers containing special characters like '&', spaces, or reserved keywords.
- Prefer CTEs for readability.
- Include a LIMIT clause unless the user asked for all rows.
- If the question is ambiguous, pick the most reasonable interpretation.
"""


class SQLGeneratorAgent(BaseAgent):
    name = "SQLGeneratorAgent"
    description = "Generates SQL from natural language, with schema awareness."

    def __init__(self, tool_registry: Any = None) -> None:
        super().__init__(tool_registry)
        self._llm = get_llm(model=os.getenv("SQL_GEN_MODEL", ""))

    async def _run(self, ctx: AgentContext, result: AgentResult) -> AgentResult:
        await self._report("Generating SQL…", 10)

        schema_text = json.dumps(ctx.schema_context, indent=2) if ctx.schema_context else "{}"
        sql = await self._generate_sql(ctx.task_description, schema_text)

        if not sql:
            result.add_step(
                action="sql_gen_failed",
                output_summary="Could not generate SQL",
                severity=Severity.ERROR,
            )
            result.output = {"sql": None}
            return result

        sql = self._sanitise(sql)
        await self._report("SQL generated, validating…", 50)
        result.add_step(action="sql_generated", output_summary=sql[:300])

        # Dry-run validation via EXPLAIN
        valid = await self._validate_sql(sql, result)

        # Execute if requested
        exec_result = None
        if valid and self.tools and ctx.metadata.get("execute", False):
            try:
                exec_result = await self.tools.call("execute_sql", query=sql)
                result.add_step(
                    action="sql_executed",
                    output_summary=str(exec_result)[:300],
                )
            except Exception as exc:
                result.add_step(
                    action="sql_exec_error",
                    output_summary=str(exc),
                    severity=Severity.ERROR,
                )

        result.output = {
            "sql": sql,
            "valid": valid,
            "executed": exec_result is not None,
            "result_preview": str(exec_result)[:500] if exec_result else None,
        }
        result.artifacts["generated_sql"] = sql
        return result

    # ------------------------------------------------------------------
    # Generation
    # ------------------------------------------------------------------
    async def _generate_sql(self, question: str, schema: str) -> Optional[str]:
        # Try LLM first
        if self._llm.is_available():
            try:
                prompt = _SQL_GEN_PROMPT.format(schema=schema, question=question)
                logger.debug("SQL generator prompt (len=%d)", len(prompt))
                text = self._llm.generate(prompt)
                logger.debug("SQL generator output (len=%d)", len(text) if text else 0)
                if text:
                    return self._strip_fences(text)
            except Exception as e:
                logger.warning("SQL generator LLM error: %s", e)

        # Try code-gen microservice
        if self.tools:
            try:
                svc_result = await self.tools.call(
                    "code_gen_service",
                    prompt=question,
                    schema_context=schema,
                )
                if isinstance(svc_result, str):
                    return self._strip_fences(svc_result)
                if isinstance(svc_result, dict) and "sql" in svc_result:
                    return svc_result["sql"]
            except Exception:
                pass

        return None

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------
    async def _validate_sql(self, sql: str, result: AgentResult) -> bool:
        if not self.tools:
            result.add_step(
                action="validation_skipped",
                output_summary="No tool registry — skipping EXPLAIN",
                severity=Severity.INFO,
            )
            return True
        try:
            explain_sql = f"EXPLAIN {sql}"
            await self.tools.call("execute_sql", query=explain_sql)
            result.add_step(action="sql_valid", output_summary="EXPLAIN passed")
            return True
        except Exception as exc:
            result.add_step(
                action="sql_invalid",
                output_summary=f"EXPLAIN failed: {exc}",
                severity=Severity.WARNING,
            )
            return False

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _strip_fences(text: str) -> str:
        text = text.strip()
        if text.startswith("```"):
            text = re.sub(r"^```\w*\n?", "", text)
        if text.endswith("```"):
            text = text[: text.rfind("```")]
        return text.strip()

    @staticmethod
    def _sanitise(sql: str) -> str:
        """Basic safety: reject destructive DDL unless explicitly allowed."""
        upper = sql.upper().strip()
        dangerous = ("DROP DATABASE", "DROP SCHEMA", "TRUNCATE", "DELETE FROM")
        for kw in dangerous:
            if upper.startswith(kw):
                return f"-- BLOCKED: starts with {kw}\n-- {sql}"
        return sql
