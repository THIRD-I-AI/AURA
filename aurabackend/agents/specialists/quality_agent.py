"""
Quality Agent
=============
Handles: Data quality checks — null rates, uniqueness, range validation, type
consistency, anomaly detection, freshness, and custom rules.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from agents.base import AgentContext, AgentResult, BaseAgent, Severity


@dataclass
class QualityCheck:
    name: str
    check_type: str  # null_rate | uniqueness | range | regex | custom_sql
    column: Optional[str] = None
    threshold: float = 0.0
    sql: str = ""
    passed: Optional[bool] = None
    actual_value: Optional[float] = None
    message: str = ""


class QualityAgent(BaseAgent):
    name = "QualityAgent"
    description = "Runs data quality checks and generates quality reports."

    async def _run(self, ctx: AgentContext, result: AgentResult) -> AgentResult:
        await self._report("Preparing quality checks…", 5)

        schema = ctx.schema_context or {}
        upstream = ctx.upstream_results or {}

        # Build checks from schema + upstream profiling
        checks = self._build_checks(schema, upstream, ctx.task_description)
        await self._report(f"Running {len(checks)} quality check(s)…", 20)

        passed, failed = 0, 0
        for i, chk in enumerate(checks):
            chk = await self._execute_check(chk, result)
            pct = 20 + int(70 * (i + 1) / max(len(checks), 1))
            await self._report(f"Check {i+1}/{len(checks)}: {chk.name}", pct)
            if chk.passed:
                passed += 1
            else:
                failed += 1

        score = round(100 * passed / max(passed + failed, 1), 1)

        result.output = {
            "score": score,
            "passed": passed,
            "failed": failed,
            "total": len(checks),
            "checks": [
                {
                    "name": c.name,
                    "type": c.check_type,
                    "column": c.column,
                    "passed": c.passed,
                    "actual": c.actual_value,
                    "threshold": c.threshold,
                    "message": c.message,
                }
                for c in checks
            ],
        }
        result.artifacts["quality_report"] = result.output

        if score < 80:
            result.suggestions.append(
                f"Quality score is {score}%. Consider running TransformAgent to clean the data."
            )
        return result

    # ------------------------------------------------------------------
    # Check generation
    # ------------------------------------------------------------------
    def _build_checks(
        self,
        schema: Dict[str, Any],
        upstream: Dict[str, Any],
        description: str,
    ) -> List[QualityCheck]:
        checks: List[QualityCheck] = []

        tables: List[Dict] = []
        # Try upstream profiling first
        for _key, val in upstream.items():
            if isinstance(val, dict):
                if "profile" in val:
                    tables.append(val["profile"])
                if "tables" in val:
                    for t in val["tables"]:
                        tables.append(t)

        # Fallback: schema_context columns
        if not tables and isinstance(schema, dict):
            for table_name, cols in schema.items():
                if isinstance(cols, list):
                    tables.append({"table": table_name, "columns": cols})

        for tbl in tables:
            table_name = tbl.get("table") or tbl.get("name") or "unknown"
            columns = tbl.get("columns") or []

            for col_info in columns:
                col_name = col_info if isinstance(col_info, str) else col_info.get("name", "")
                if not col_name:
                    continue

                # NULL check
                checks.append(
                    QualityCheck(
                        name=f"{table_name}.{col_name}_null_rate",
                        check_type="null_rate",
                        column=col_name,
                        threshold=0.10,
                        sql=(
                            f"SELECT ROUND(COUNT(*) FILTER (WHERE \"{col_name}\" IS NULL)::numeric "
                            f"/ GREATEST(COUNT(*), 1), 4) AS null_rate FROM \"{table_name}\";"
                        ),
                    )
                )

                # Uniqueness (for id-like columns)
                if any(kw in col_name.lower() for kw in ("id", "key", "code", "uuid")):
                    checks.append(
                        QualityCheck(
                            name=f"{table_name}.{col_name}_uniqueness",
                            check_type="uniqueness",
                            column=col_name,
                            threshold=1.0,
                            sql=(
                                f"SELECT ROUND(COUNT(DISTINCT \"{col_name}\")::numeric "
                                f"/ GREATEST(COUNT(*), 1), 4) AS uniqueness "
                                f"FROM \"{table_name}\";"
                            ),
                        )
                    )

        # Row-count check per table
        seen_tables = {t.get("table") or t.get("name") for t in tables}
        for tname in seen_tables:
            if tname and tname != "unknown":
                checks.append(
                    QualityCheck(
                        name=f"{tname}_row_count",
                        check_type="row_count",
                        threshold=1.0,
                        sql=f'SELECT COUNT(*) AS cnt FROM "{tname}";',
                    )
                )

        # Always include at least one global check
        if not checks:
            checks.append(
                QualityCheck(
                    name="global_health",
                    check_type="custom_sql",
                    sql="SELECT 1 AS ok;",
                    threshold=1.0,
                )
            )

        return checks

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------
    async def _execute_check(
        self, chk: QualityCheck, result: AgentResult
    ) -> QualityCheck:
        result.add_step(
            action="quality_check",
            tool_name="execute_sql",
            input_summary=f"{chk.check_type}: {chk.name}",
        )

        if self.tools and chk.sql:
            try:
                exec_result = await self.tools.call("execute_sql", query=chk.sql)
                value = self._extract_value(exec_result)
                chk.actual_value = value
                chk.passed = self._evaluate(chk, value)
                chk.message = f"Value={value}, threshold={chk.threshold}"
                result.add_step(
                    action="check_result",
                    output_summary=chk.message,
                    severity=Severity.INFO if chk.passed else Severity.WARNING,
                )
            except Exception as exc:
                chk.passed = False
                chk.message = f"Error: {exc}"
                result.add_step(
                    action="check_error",
                    output_summary=str(exc),
                    severity=Severity.ERROR,
                )
        else:
            chk.passed = True
            chk.message = "Dry run — no tool registry"

        return chk

    @staticmethod
    def _extract_value(exec_result: Any) -> float:
        if isinstance(exec_result, (int, float)):
            return float(exec_result)
        if isinstance(exec_result, dict):
            for v in exec_result.values():
                if isinstance(v, (int, float)):
                    return float(v)
                if isinstance(v, list) and v:
                    row = v[0]
                    if isinstance(row, dict):
                        for rv in row.values():
                            if isinstance(rv, (int, float)):
                                return float(rv)
        return 0.0

    @staticmethod
    def _evaluate(chk: QualityCheck, value: float) -> bool:
        if chk.check_type == "null_rate":
            return value <= chk.threshold
        if chk.check_type == "uniqueness":
            return value >= chk.threshold
        if chk.check_type == "row_count":
            return value >= chk.threshold
        return value >= chk.threshold
