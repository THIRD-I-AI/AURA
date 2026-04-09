import math
import statistics
from typing import Any, Dict, List, Optional

from agents.base import AgentContext, AgentResult, AgentStatus, BaseAgent
from shared.llm_provider import get_llm


# ---------------------------------------------------------------------------
# Pure-Python statistical helpers (no pandas / numpy dependency)
# ---------------------------------------------------------------------------

def _safe_float(v: Any) -> Optional[float]:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _numeric_values(records: List[Dict[str, Any]], col: str) -> List[float]:
    return [f for r in records if (f := _safe_float(r.get(col))) is not None]


def _describe_column(values: List[float]) -> Dict[str, Any]:
    """Return descriptive statistics for a numeric series."""
    n = len(values)
    if n == 0:
        return {}
    sorted_v = sorted(values)
    mean = statistics.mean(values)
    result: Dict[str, Any] = {
        "count": n,
        "mean": round(mean, 4),
        "min": sorted_v[0],
        "max": sorted_v[-1],
        "range": round(sorted_v[-1] - sorted_v[0], 4),
    }
    if n >= 2:
        result["std"] = round(statistics.stdev(values), 4)
        result["median"] = round(statistics.median(values), 4)
        result["p25"] = round(sorted_v[max(0, int(n * 0.25) - 1)], 4)
        result["p75"] = round(sorted_v[min(n - 1, int(n * 0.75))], 4)
    return result


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
    """Pearson correlation coefficient between two numeric lists."""
    n = min(len(x), len(y))
    if n < 3:
        return None
    x, y = x[:n], y[:n]
    mx, my = statistics.mean(x), statistics.mean(y)
    num = sum((xi - mx) * (yi - my) for xi, yi in zip(x, y))
    den_x = math.sqrt(sum((xi - mx) ** 2 for xi in x))
    den_y = math.sqrt(sum((yi - my) ** 2 for yi in y))
    if den_x == 0 or den_y == 0:
        return None
    return round(num / (den_x * den_y), 4)


def _build_stats_summary(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Build a comprehensive statistical summary of a result set."""
    if not records:
        return {}

    columns = list(records[0].keys())
    numeric_cols = [c for c in columns if _numeric_values(records[:5], c)]

    summary: Dict[str, Any] = {
        "row_count": len(records),
        "column_count": len(columns),
        "numeric_columns": numeric_cols,
        "descriptive_stats": {},
        "outliers": {},
        "correlations": {},
    }

    col_values: Dict[str, List[float]] = {}
    for col in numeric_cols[:6]:  # cap at 6 cols for speed
        vals = _numeric_values(records, col)
        col_values[col] = vals
        summary["descriptive_stats"][col] = _describe_column(vals)
        outliers = _detect_outliers(vals)
        if outliers:
            summary["outliers"][col] = {
                "count": len(outliers),
                "values": outliers[:5],
            }

    # Pairwise correlations for first 4 numeric columns
    num_cols = list(col_values.keys())[:4]
    for i in range(len(num_cols)):
        for j in range(i + 1, len(num_cols)):
            a, b = num_cols[i], num_cols[j]
            corr = _pearson_correlation(col_values[a], col_values[b])
            if corr is not None:
                summary["correlations"][f"{a} vs {b}"] = corr

    return summary


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------

class AnalysisAgent(BaseAgent):
    """
    Analyzes raw data results (from SQL execution) and synthesizes a concise,
    natural language conclusion.  Computes descriptive statistics, detects
    outliers, and calculates pairwise correlations — all in pure Python — then
    feeds the summary to the LLM for a final narrative.
    """

    name = "AnalysisAgent"
    description = "Analyzes tabular data results and synthesizes a targeted narrative conclusion."

    async def _run(self, ctx: AgentContext, result: AgentResult) -> AgentResult:
        # Pull records from any upstream result that contains them
        upstream_data = None
        for dep_result in ctx.upstream_results.values():
            if isinstance(dep_result, dict) and "records" in dep_result:
                upstream_data = dep_result
                break

        records = upstream_data["records"] if upstream_data else []

        if not records:
            result.status = AgentStatus.SUCCESS
            result.output["conclusion"] = None
            result.output["stats"] = {}
            return result

        try:
            # --- Step 1: Statistical analysis ---
            result.add_step(
                action="compute_statistics",
                input_summary=f"Computing stats for {len(records)} rows.",
            )

            stats = _build_stats_summary(records)
            result.output["stats"] = stats

            stats_lines = []
            for col, desc in stats.get("descriptive_stats", {}).items():
                parts = [f"{k}={v}" for k, v in desc.items()]
                stats_lines.append(f"  {col}: {', '.join(parts)}")

            outlier_lines = []
            for col, info in stats.get("outliers", {}).items():
                outlier_lines.append(f"  {col}: {info['count']} outlier(s) detected")

            corr_lines = []
            for pair, corr in stats.get("correlations", {}).items():
                strength = (
                    "strong" if abs(corr) >= 0.7
                    else "moderate" if abs(corr) >= 0.4
                    else "weak"
                )
                direction = "positive" if corr >= 0 else "negative"
                corr_lines.append(f"  {pair}: r={corr} ({strength} {direction})")

            stats_text = "\n".join(stats_lines) if stats_lines else "  (no numeric columns)"
            outliers_text = "\n".join(outlier_lines) if outlier_lines else "  None detected"
            correlations_text = "\n".join(corr_lines) if corr_lines else "  N/A"

            result.add_step(
                action="synthesize_conclusion",
                input_summary=f"Sending statistical summary to LLM for narrative.",
            )

            # --- Step 2: LLM narrative ---
            llm = get_llm()
            limited_records = records[:20]
            conclusion_prompt = (
                f"User asked: '{ctx.user_prompt}'\n\n"
                f"Data summary ({len(records)} rows, {stats['column_count']} columns):\n"
                f"Descriptive statistics:\n{stats_text}\n\n"
                f"Outliers:\n{outliers_text}\n\n"
                f"Correlations:\n{correlations_text}\n\n"
                f"Sample data (first 20 rows):\n{limited_records}\n\n"
                "Based on these statistics and sample data, provide a brief, conclusive answer "
                "to the user's question. Highlight any interesting trends, outliers, or "
                "correlations. Keep it to 2-4 sentences in a friendly, data-driven tone. "
                "Do not show SQL."
            )

            conclusion = llm.generate(conclusion_prompt)

            result.add_step(
                action="conclusion_ready",
                output_summary=f"Conclusion: {len(conclusion or '')} chars, "
                               f"{len(stats.get('descriptive_stats', {}))} numeric columns analysed.",
            )

            result.status = AgentStatus.SUCCESS
            result.output["conclusion"] = conclusion

        except Exception as e:
            result.status = AgentStatus.FAILED
            result.error = f"Analysis failed: {str(e)}"
            result.add_step(action="analysis_error", output_summary=str(e))

        return result
