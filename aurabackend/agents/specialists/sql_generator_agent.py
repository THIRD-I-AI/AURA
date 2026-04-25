"""
SQL Generator Agent
===================
Handles: Natural-language → SQL generation.  Wraps the existing AURA code-gen
service and adds schema-aware context injection + validation.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional, cast

try:
    import sqlglot
    from sqlglot import exp as _sqlglot_exp
    _SQLGLOT_AVAILABLE = True
except ImportError:  # pragma: no cover — fallback handled at runtime
    sqlglot = None  # type: ignore[assignment]
    _sqlglot_exp = None  # type: ignore[assignment]
    _SQLGLOT_AVAILABLE = False

from agents.base import AgentContext, AgentResult, AgentStatus, BaseAgent, Severity
from agents.params import SQLGeneratorAgentParams

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


_SQL_EXPLAIN_PROMPT = """\
Explain the following SQL query in ONE plain-English sentence (under 200 chars).
Focus on what it returns, not how. No markdown, no quoting of identifiers.

SQL:
{sql}
"""


class SQLGeneratorAgent(BaseAgent):
    name = "SQLGeneratorAgent"
    description = "Generates SQL from natural language, with schema awareness."
    llm_model_env = "SQL_GEN_MODEL"

    async def _run(self, ctx: AgentContext, result: AgentResult) -> AgentResult:
        await self._report("Generating SQL…", 10)

        schema_text = json.dumps(ctx.schema_context, indent=2) if ctx.schema_context else "{}"
        sql, gen_error = await self._generate_sql(ctx.task_description, schema_text)

        if not sql:
            reason = gen_error or (
                "No LLM provider available — install Ollama or set GROQ_API_KEY/GEMINI_API_KEY."
                if not self._llm.is_available() else
                "LLM returned an empty SQL response."
            )
            result.add_step(
                action="sql_gen_failed",
                output_summary=reason,
                severity=Severity.ERROR,
            )
            result.error = f"SQL generation failed: {reason}"
            result.status = AgentStatus.FAILED
            result.output = {"sql": None}
            return result

        sql = self._sanitise(sql)
        await self._report("SQL generated, validating…", 50)
        result.add_step(action="sql_generated", output_summary=sql[:300])

        # Best-effort NL explanation (cached at the provider layer).
        explanation = await self._explain_sql(sql)

        # Dry-run validation via EXPLAIN
        valid = await self._validate_sql(sql, result)

        # Execute if requested
        exec_result = None
        params = cast(SQLGeneratorAgentParams, ctx.metadata or {})
        if valid and self.tools and params.get("execute", False):
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
            "explanation": explanation,
        }
        result.artifacts["generated_sql"] = sql
        if explanation:
            result.artifacts["sql_explanation"] = explanation
        return result

    # ------------------------------------------------------------------
    # Generation
    # ------------------------------------------------------------------
    async def _generate_sql(self, question: str, schema: str) -> tuple[Optional[str], Optional[str]]:
        """Return (sql, error_reason). Either field may be set."""
        last_error: Optional[str] = None

        # Try LLM first
        if self._llm.is_available():
            try:
                prompt = _SQL_GEN_PROMPT.format(schema=schema, question=question)
                logger.debug("SQL generator prompt (len=%d)", len(prompt))
                text = self._llm.generate(prompt)
                logger.debug("SQL generator output (len=%d)", len(text) if text else 0)
                if text:
                    return self._strip_fences(text), None
                last_error = "LLM returned empty response."
            except Exception as e:
                last_error = f"LLM error: {e}"
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
                    return self._strip_fences(svc_result), None
                if isinstance(svc_result, dict) and "sql" in svc_result:
                    return svc_result["sql"], None
                last_error = f"code-gen service returned unexpected payload: {type(svc_result).__name__}"
            except Exception as e:
                last_error = f"code-gen service error: {e}"
                logger.warning("code-gen service error: %s", e)

        return None, last_error

    # ------------------------------------------------------------------
    # Explanation (opt-in, cached via _CachedProvider at the LLM boundary)
    # ------------------------------------------------------------------
    async def _explain_sql(self, sql: str) -> Optional[str]:
        """Best-effort one-sentence NL explanation of the generated SQL.

        Short-circuits on any failure — this is a nice-to-have and must
        never block or break the main generation path.
        """
        import os
        if os.getenv("AURA_EXPLAIN_SQL", "1") == "0":
            return None
        if not self._llm.is_available():
            return None
        try:
            prompt = _SQL_EXPLAIN_PROMPT.format(sql=sql[:2000])
            text = self._llm.generate(prompt)
            if not text:
                return None
            # Collapse whitespace, strip fences, cap length.
            text = self._strip_fences(text).replace("\n", " ").strip()
            if len(text) > 240:
                text = text[:237] + "…"
            return text or None
        except Exception as exc:
            logger.debug("sql explanation skipped: %s", exc)
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

    # Regex denylist used as a last-resort fallback when the SQL parser is
    # unavailable. The parser-based check is the primary defense; it sees
    # through leading comments, whitespace, and multi-statement payloads
    # that fool the regex.
    _DESTRUCTIVE_REGEX = re.compile(
        r"\b(DROP\s+(?:DATABASE|SCHEMA)|TRUNCATE|DELETE\s+FROM)\b",
        re.IGNORECASE,
    )

    @classmethod
    def _sanitise(cls, sql: str) -> str:
        """Reject destructive DDL/DML using a real SQL parser.

        Blocks any statement (anywhere in the payload) that is:
          - DROP DATABASE / DROP SCHEMA
          - TRUNCATE
          - DELETE FROM
        DROP TABLE/INDEX, CREATE, INSERT, UPDATE remain allowed because the
        agentic transform/optimization flows depend on them.
        """
        if _SQLGLOT_AVAILABLE:
            try:
                statements = sqlglot.parse(sql, dialect="postgres")
            except Exception:
                statements = None
            if statements is not None:
                for stmt in statements:
                    if stmt is None:
                        continue
                    if isinstance(stmt, _sqlglot_exp.Drop):
                        kind = (stmt.args.get("kind") or "").upper()
                        if kind in ("DATABASE", "SCHEMA"):
                            return f"-- BLOCKED: DROP {kind} is not permitted\n-- {sql}"
                    elif isinstance(stmt, _sqlglot_exp.TruncateTable):
                        return f"-- BLOCKED: TRUNCATE is not permitted\n-- {sql}"
                    elif isinstance(stmt, _sqlglot_exp.Delete):
                        return f"-- BLOCKED: DELETE FROM is not permitted\n-- {sql}"
                return sql
            # Parser failed entirely — fall through to regex fallback.

        # Regex fallback (also used when sqlglot isn't installed).
        match = cls._DESTRUCTIVE_REGEX.search(sql)
        if match:
            return f"-- BLOCKED: contains {match.group(0).upper()}\n-- {sql}"
        return sql
