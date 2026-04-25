"""
Analysis Agent
==============
Synthesises a concise narrative answer to the user's question from the
ExecutionAgent's records, the SQL that produced them, and the chart spec
chosen by VisualizationAgent.

The agent computes descriptive stats, IQR-based outliers, and pairwise
correlations in pure Python, then prompts the LLM with that context plus the
column profile and a small sample of rows.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional

from agents.base import AgentContext, AgentResult, AgentStatus, BaseAgent
from shared.data_profile import (
    describe_column,
    numeric_values,
    profile_columns,
    profile_to_text,
    safe_float,
)

# Backward-compat re-exports (some callers may still reference these names)
_safe_float = safe_float
_numeric_values = numeric_values
_describe_column = describe_column


# ---------------------------------------------------------------------------
# Pure-Python statistical helpers (kept agent-local; reused by _build_stats)
# ---------------------------------------------------------------------------

def _detect_outliers(values: List[float]) -> List[float]:
    """IQR-based outlier detection."""
    if len(values) < 4:
        return []
    sorted_v = sorted(values)
    n = len(sorted_v)
    q1 = sorted_v[int(n * 0.25)]
    q3 = sorted_v[int(n * 0.75)]
    iqr = q3 - q1
    if iqr == 0:
        return []
    lower, upper = q1 - 1.5 * iqr, q3 + 1.5 * iqr
    return [v for v in values if v < lower or v > upper]


def _pearson_correlation(x: List[float], y: List[float]) -> Optional[float]:
    n = min(len(x), len(y))
    if n < 3:
        return None
    x, y = x[:n], y[:n]
    mx, my = sum(x) / n, sum(y) / n
    num = sum((xi - mx) * (yi - my) for xi, yi in zip(x, y))
    den_x = math.sqrt(sum((xi - mx) ** 2 for xi in x))
    den_y = math.sqrt(sum((yi - my) ** 2 for yi in y))
    if den_x == 0 or den_y == 0:
        return None
    return round(num / (den_x * den_y), 4)


def _build_stats_summary(
    records: List[Dict[str, Any]],
    profiles: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    if not records:
        return {}

    columns = list(records[0].keys())
    numeric_cols = [c for c, p in profiles.items() if p["dtype"] == "numeric"]

    summary: Dict[str, Any] = {
        "row_count": len(records),
        "column_count": len(columns),
        "numeric_columns": numeric_cols,
        "descriptive_stats": {},
        "outliers": {},
        "correlations": {},
    }

    col_values: Dict[str, List[float]] = {}
    for col in numeric_cols[:6]:
        vals = numeric_values(records, col)
        col_values[col] = vals
        summary["descriptive_stats"][col] = describe_column(vals)
        outliers = _detect_outliers(vals)
        if outliers:
            summary["outliers"][col] = {
                "count": len(outliers),
                "values": outliers[:5],
            }

    num_cols = list(col_values.keys())[:4]
    for i in range(len(num_cols)):
        for j in range(i + 1, len(num_cols)):
            a, b = num_cols[i], num_cols[j]
            corr = _pearson_correlation(col_values[a], col_values[b])
            if corr is not None:
                summary["correlations"][f"{a} vs {b}"] = corr

    return summary


def _format_stats_text(stats: Dict[str, Any]) -> str:
    lines = []
    for col, desc in stats.get("descriptive_stats", {}).items():
        parts = [f"{k}={v}" for k, v in desc.items()]
        lines.append(f"  {col}: {', '.join(parts)}")
    return "\n".join(lines) if lines else "  (no numeric columns)"


def _format_outliers_text(stats: Dict[str, Any]) -> str:
    lines = [
        f"  {col}: {info['count']} outlier(s) detected (e.g. {info['values'][:3]})"
        for col, info in stats.get("outliers", {}).items()
    ]
    return "\n".join(lines) if lines else "  None detected"


def _format_correlations_text(stats: Dict[str, Any]) -> str:
    lines = []
    for pair, corr in stats.get("correlations", {}).items():
        strength = (
            "strong" if abs(corr) >= 0.7
            else "moderate" if abs(corr) >= 0.4
            else "weak"
        )
        direction = "positive" if corr >= 0 else "negative"
        lines.append(f"  {pair}: r={corr} ({strength} {direction})")
    return "\n".join(lines) if lines else "  N/A"


def _extract_upstream(ctx: AgentContext) -> Dict[str, Any]:
    """Walk upstream_results to gather records, sql, chart_spec."""
    bag: Dict[str, Any] = {"records": [], "columns": [], "sql": None, "chart_spec": None}
    for dep_result in ctx.upstream_results.values():
        if not isinstance(dep_result, dict):
            continue
        if not bag["records"] and "records" in dep_result:
            bag["records"] = dep_result.get("records") or []
            bag["columns"] = dep_result.get("columns") or []
        if not bag["sql"] and dep_result.get("sql"):
            bag["sql"] = dep_result["sql"]
        if not bag["chart_spec"] and dep_result.get("chart_spec"):
            bag["chart_spec"] = dep_result["chart_spec"]
    return bag


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------

class AnalysisAgent(BaseAgent):
    """Analyses tabular results and synthesises a targeted narrative answer."""

    name = "AnalysisAgent"
    description = "Synthesises a narrative answer from query results, SQL, and chart spec."

    async def _run(self, ctx: AgentContext, result: AgentResult) -> AgentResult:
        upstream = _extract_upstream(ctx)
        records: List[Dict[str, Any]] = upstream["records"]

        if not records:
            result.status = AgentStatus.SUCCESS
            result.output["conclusion"] = None
            result.output["stats"] = {}
            return result

        try:
            result.add_step(
                action="compute_statistics",
                input_summary=f"Computing stats for {len(records)} rows.",
            )

            profiles = profile_columns(records, upstream["columns"] or list(records[0].keys()))
            stats = _build_stats_summary(records, profiles)
            result.output["stats"] = stats

            result.add_step(
                action="synthesize_conclusion",
                input_summary="Sending statistical summary + SQL + chart context to LLM.",
            )

            chart = upstream["chart_spec"] or {}
            chart_line = (
                f"Chart chosen: {chart.get('type')} "
                f"(x={chart.get('x')}, y={chart.get('y')}). "
                f"Reason: {chart.get('reason', 'n/a')}"
                if chart else "Chart chosen: none"
            )

            sql_block = upstream["sql"] or "(SQL not available)"

            conclusion_prompt = (
                f"USER QUESTION:\n{ctx.user_prompt}\n\n"
                f"EXECUTED SQL:\n{sql_block}\n\n"
                f"DATA SUMMARY ({len(records)} rows, {stats.get('column_count', 0)} columns):\n"
                f"Column profile:\n{profile_to_text(profiles)}\n\n"
                f"Descriptive statistics:\n{_format_stats_text(stats)}\n\n"
                f"Outliers:\n{_format_outliers_text(stats)}\n\n"
                f"Correlations:\n{_format_correlations_text(stats)}\n\n"
                f"{chart_line}\n\n"
                f"Sample rows (first 8):\n{records[:8]}\n\n"
                "Write a concise data-driven answer for the user. Rules:\n"
                "1. The first sentence MUST directly answer the user's question using the numbers above.\n"
                "2. Add 1–2 sentences of supporting evidence (a stat, a correlation, an outlier) "
                "only if it would change a decision.\n"
                "3. Do not describe the chart visually. Do not show SQL. Do not greet.\n"
                "4. Stay under 4 sentences total. Friendly, professional tone."
            )

            conclusion = self.llm.generate(conclusion_prompt)

            result.add_step(
                action="conclusion_ready",
                output_summary=(
                    f"Conclusion: {len(conclusion or '')} chars, "
                    f"{len(stats.get('descriptive_stats', {}))} numeric columns analysed."
                ),
            )

            result.status = AgentStatus.SUCCESS
            result.output["conclusion"] = conclusion

        except Exception as e:
            result.status = AgentStatus.FAILED
            result.error = f"Analysis failed: {e}"
            result.add_step(action="analysis_error", output_summary=str(e))

        return result
