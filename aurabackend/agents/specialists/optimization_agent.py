"""
Optimization Agent
==================
Handles: Query performance analysis, index recommendations, partitioning
strategy, materialized view suggestions, and EXPLAIN plan interpretation.
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

from agents.base import AgentContext, AgentResult, BaseAgent, Severity

try:
    import google.generativeai as genai
except ImportError:
    genai = None  # type: ignore


_OPTIMIZE_PROMPT = """\
You are a database performance tuning expert.  Given the schema and query
patterns, suggest concrete optimisations.

SCHEMA:
{schema}

QUERY/TASK:
{request}

Return ONLY valid JSON (no markdown):
{{
  "indexes": [
    {{"table": "...", "columns": ["..."], "type": "btree|hash|gin", "reason": "..."}}
  ],
  "partitioning": [
    {{"table": "...", "column": "...", "strategy": "range|list|hash", "reason": "..."}}
  ],
  "materialized_views": [
    {{"name": "...", "sql": "CREATE MATERIALIZED VIEW ...", "reason": "..."}}
  ],
  "query_rewrites": [
    {{"original_issue": "...", "suggestion": "...", "rewritten_sql": "..."}}
  ]
}}
"""


class OptimizationAgent(BaseAgent):
    name = "OptimizationAgent"
    description = "Analyses and optimises database performance."

    def __init__(self, tool_registry: Any = None) -> None:
        super().__init__(tool_registry)
        self._model = self._init_model()

    @staticmethod
    def _init_model() -> Any:
        if genai is None:
            return None
        api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        if not api_key:
            return None
        try:
            configure_fn = getattr(genai, "configure", None)
            if callable(configure_fn):
                configure_fn(api_key=api_key)
            model_cls = getattr(genai, "GenerativeModel", None)
            if model_cls:
                return model_cls(os.getenv("OPTIMIZE_MODEL", "gemini-2.5-flash"))
        except Exception:
            pass
        return None

    async def _run(self, ctx: AgentContext, result: AgentResult) -> AgentResult:
        await self._report("Analysing schema for optimisation opportunities…", 10)

        schema_text = json.dumps(ctx.schema_context, indent=2) if ctx.schema_context else "{}"
        recommendations = await self._generate_recommendations(ctx.task_description, schema_text)

        if not recommendations:
            result.add_step(
                action="no_recommendations",
                output_summary="No optimisation recommendations generated",
                severity=Severity.INFO,
            )
            result.output = {"recommendations": {}}
            return result

        await self._report("Recommendations ready", 60)

        # Try to run EXPLAIN on upstream queries for concrete plan analysis
        explain_results: List[Dict[str, Any]] = []
        upstream_sqls = self._collect_upstream_sqls(ctx.upstream_results or {})
        for sql in upstream_sqls[:5]:  # Cap at 5
            explain_result = await self._run_explain(sql, result)
            if explain_result:
                explain_results.append(explain_result)

        # Apply non-destructive recommendations (CREATE INDEX)
        applied = 0
        indexes = recommendations.get("indexes", [])
        for idx in indexes:
            if self.tools:
                try:
                    idx_sql = self._build_create_index(idx)
                    if idx_sql:
                        await self.tools.call("execute_sql", query=idx_sql)
                        applied += 1
                        result.add_step(
                            action="index_created",
                            output_summary=idx_sql[:200],
                        )
                except Exception as exc:
                    result.add_step(
                        action="index_error",
                        output_summary=str(exc),
                        severity=Severity.WARNING,
                    )

        result.output = {
            "recommendations": recommendations,
            "indexes_applied": applied,
            "explain_plans": explain_results,
        }
        result.artifacts["optimization_report"] = recommendations
        result.suggestions.extend(self._format_suggestions(recommendations))
        return result

    # ------------------------------------------------------------------
    # Recommendation generation
    # ------------------------------------------------------------------
    async def _generate_recommendations(self, request: str, schema: str) -> Dict[str, Any]:
        if self._model:
            try:
                prompt = _OPTIMIZE_PROMPT.format(schema=schema, request=request)
                response = self._model.generate_content(prompt)
                text = (response.text or "").strip()
                if text.startswith("```"):
                    text = text.split("\n", 1)[-1]
                if text.endswith("```"):
                    text = text.rsplit("```", 1)[0]
                parsed = json.loads(text.strip())
                if isinstance(parsed, dict):
                    return parsed
            except Exception:
                pass

        return self._fallback_recommendations(request)

    @staticmethod
    def _fallback_recommendations(request: str) -> Dict[str, Any]:
        return {
            "indexes": [],
            "partitioning": [],
            "materialized_views": [],
            "query_rewrites": [
                {
                    "original_issue": "General advice",
                    "suggestion": "Run EXPLAIN ANALYZE on slow queries to identify bottlenecks",
                    "rewritten_sql": "",
                }
            ],
        }

    # ------------------------------------------------------------------
    # EXPLAIN helper
    # ------------------------------------------------------------------
    async def _run_explain(self, sql: str, result: AgentResult) -> Optional[Dict[str, Any]]:
        if not self.tools:
            return None
        try:
            explain_sql = f"EXPLAIN (FORMAT JSON, ANALYZE false) {sql}"
            plan = await self.tools.call("execute_sql", query=explain_sql)
            result.add_step(action="explain_plan", output_summary=str(plan)[:300])
            return {"sql": sql[:200], "plan": plan}
        except Exception:
            return None

    # ------------------------------------------------------------------
    # SQL helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _build_create_index(idx: Dict[str, Any]) -> Optional[str]:
        table = idx.get("table")
        columns = idx.get("columns")
        idx_type = idx.get("type", "btree")
        if not table or not columns:
            return None
        col_list = ", ".join(f'"{c}"' for c in columns)
        idx_name = f"idx_{'_'.join(columns)}_{table}"[:63]
        return f'CREATE INDEX IF NOT EXISTS "{idx_name}" ON "{table}" USING {idx_type} ({col_list});'

    @staticmethod
    def _collect_upstream_sqls(upstream: Dict[str, Any]) -> List[str]:
        sqls: List[str] = []
        for _key, val in upstream.items():
            if isinstance(val, dict):
                if "sql" in val:
                    sqls.append(val["sql"])
                for sub in val.get("transforms", []):
                    if isinstance(sub, str):
                        sqls.append(sub)
        return sqls

    @staticmethod
    def _format_suggestions(recs: Dict[str, Any]) -> List[str]:
        suggestions: List[str] = []
        for mv in recs.get("materialized_views", []):
            suggestions.append(f"Create materialized view: {mv.get('name')} — {mv.get('reason', '')}")
        for part in recs.get("partitioning", []):
            suggestions.append(
                f"Partition {part.get('table')} by {part.get('column')} "
                f"({part.get('strategy')}) — {part.get('reason', '')}"
            )
        for rw in recs.get("query_rewrites", []):
            if rw.get("suggestion"):
                suggestions.append(rw["suggestion"])
        return suggestions
