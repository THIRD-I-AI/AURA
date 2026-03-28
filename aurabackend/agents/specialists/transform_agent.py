"""
Transform Agent
================
Handles: SQL-based data transformations — joins, aggregations, window functions,
pivots, type casting, deduplication, and derived column creation.
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

from agents.base import AgentContext, AgentResult, BaseAgent, Severity
from shared.llm_provider import get_llm


_TRANSFORM_PROMPT = """\
You are a senior data engineer.  Given the user's transformation request and the
available schema, generate one or more SQL statements (PostgreSQL dialect) that
implement the transformation.

SCHEMA:
{schema}

USER REQUEST:
{request}

RULES:
- Use CTEs for readability.
- Always alias computed columns.
- Always enclose ALL table and column names in double quotes (e.g., "my_table"."my_column") to ensure compatibility with identifiers containing special characters like '&', spaces, or reserved keywords.
- If asked to "clean", handle: trim whitespace, cast types, remove duplicates.
- If asked to "join", infer join keys from column names.
- Return ONLY a JSON array of SQL strings.  No markdown.

EXAMPLE OUTPUT:
["WITH cleaned AS (SELECT DISTINCT ... FROM raw_data) SELECT * FROM cleaned"]
"""


class TransformAgent(BaseAgent):
    name = "TransformAgent"
    description = "Applies SQL-based data transformations."

    def __init__(self, tool_registry: Any = None) -> None:
        super().__init__(tool_registry)
        self._llm = get_llm(model=os.getenv("TRANSFORM_MODEL", ""))

    async def _run(self, ctx: AgentContext, result: AgentResult) -> AgentResult:
        await self._report("Planning transformations…", 10)

        schema_text = json.dumps(ctx.schema_context, indent=2) if ctx.schema_context else "No schema available."
        sql_statements = await self._generate_transforms(ctx.task_description, schema_text)

        if not sql_statements:
            result.add_step(
                action="no_transforms",
                output_summary="Could not generate transformation SQL",
                severity=Severity.WARNING,
            )
            result.output = {"transforms": [], "executed": 0}
            return result

        await self._report(f"Generated {len(sql_statements)} transform(s)…", 40)

        # Execute each transform
        executed: List[Dict[str, Any]] = []
        for i, sql in enumerate(sql_statements):
            result.add_step(
                action="transform_sql",
                tool_name="execute_sql",
                input_summary=sql[:200],
            )

            if self.tools:
                try:
                    exec_result = await self.tools.call("execute_sql", query=sql)
                    executed.append({"sql": sql, "status": "success", "result": exec_result})
                    result.add_step(
                        action="transform_executed",
                        output_summary=f"Transform {i+1}/{len(sql_statements)} succeeded",
                    )
                except Exception as exc:
                    executed.append({"sql": sql, "status": "error", "error": str(exc)})
                    result.add_step(
                        action="transform_error",
                        output_summary=str(exc),
                        severity=Severity.ERROR,
                    )
            else:
                executed.append({"sql": sql, "status": "dry_run"})

        result.output = {
            "transforms": sql_statements,
            "executed": len([e for e in executed if e["status"] == "success"]),
            "total": len(sql_statements),
        }
        result.artifacts["sql"] = sql_statements
        result.artifacts["execution_log"] = executed
        return result

    async def _generate_transforms(self, request: str, schema: str) -> List[str]:
        if self._llm.is_available():
            try:
                prompt = _TRANSFORM_PROMPT.format(schema=schema, request=request)
                parsed = self._llm.generate_json(prompt)
                if isinstance(parsed, list):
                    return [s for s in parsed if isinstance(s, str)]
            except Exception:
                pass

        # Fallback: return generic transforms based on keywords
        return self._fallback_transforms(request)

    @staticmethod
    def _fallback_transforms(request: str) -> List[str]:
        req = request.lower()
        sqls: List[str] = []

        if "dedup" in req or "duplicate" in req:
            sqls.append(
                "-- Deduplicate\n"
                "CREATE TABLE cleaned_data AS\n"
                "SELECT DISTINCT * FROM raw_data;"
            )
        if "clean" in req or "trim" in req:
            sqls.append(
                "-- Clean whitespace\n"
                "UPDATE raw_data SET \n"
                "  col = TRIM(col)\n"
                "WHERE col IS NOT NULL;"
            )
        if "join" in req or "merge" in req:
            sqls.append(
                "-- Join example (customize table/column names)\n"
                "SELECT a.*, b.*\n"
                "FROM table_a a\n"
                "INNER JOIN table_b b ON a.id = b.id;"
            )
        if not sqls:
            sqls.append("SELECT * FROM raw_data LIMIT 100;")
        return sqls
