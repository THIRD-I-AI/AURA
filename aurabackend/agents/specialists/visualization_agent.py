"""
Visualization Agent
===================
Picks an appropriate chart spec for the records produced by ExecutionAgent.

Strategy (in order):
  1. Profile the result columns (dtype, cardinality, sample).
  2. Ask the LLM for a JSON chart spec, given the user's question + profile +
     a closed list of supported chart types.
  3. Validate the LLM response against the actual columns. If invalid, fall
     back to a *typed* heuristic that picks a chart from the column profile
     (not from keyword matching on the user's prompt).
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from agents.base import AgentContext, AgentResult, AgentStatus, BaseAgent, Severity
from shared.data_profile import profile_columns, profile_to_text

logger = logging.getLogger("aura.agents.visualization")

SUPPORTED_CHART_TYPES = {
    "bar", "stacked_bar", "line", "multi_line", "area",
    "pie", "scatter", "histogram", "kpi", "table",
}

_CHART_PROMPT = """\
You are a data visualization expert. Pick the single best chart for the
result set below.

USER QUESTION:
{question}

COLUMN PROFILE ({n_rows} rows total):
{profile}

SAMPLE ROWS (first 5):
{sample}

SUPPORTED CHART TYPES: {types}

Rules:
- Choose the type whose shape matches the data and the question.
  • date + numeric         → line / multi_line / area
  • low-card categorical + numeric → bar
  • two numerics           → scatter
  • single numeric column  → histogram
  • single value / 1 row   → kpi
  • >12 categories         → bar (NOT pie)
  • use stacked_bar only when there is a 2nd categorical to stack by
- "x" must be one column name from the profile; "y" must be a list of one or
  more numeric column names from the profile.
- "title" is a short, plain-English label.
- "reason" is one sentence explaining why this chart suits the data.

Return ONLY valid JSON, no markdown:
{{"type": "<one of supported>", "x": "<col>", "y": ["<col>", ...], "title": "<short>", "reason": "<one sentence>"}}
"""


class VisualizationAgent(BaseAgent):
    """Suggests an appropriate chart spec for the upstream query results."""

    name = "VisualizationAgent"
    description = "Picks a chart spec from query output using an LLM + column profile."

    async def _run(self, ctx: AgentContext, result: AgentResult) -> AgentResult:
        upstream_data = _find_upstream_records(ctx)
        if not upstream_data or not upstream_data.get("records"):
            result.status = AgentStatus.SUCCESS
            result.output = {"chart_spec": None}
            return result

        records: List[Dict[str, Any]] = upstream_data["records"]
        columns: List[str] = upstream_data.get("columns") or list(records[0].keys())

        profiles = profile_columns(records, columns)

        # 1. LLM attempt
        spec = self._llm_chart_spec(ctx.user_prompt, profiles, records)

        # 2. Validate / fall back
        if not _is_valid_spec(spec, profiles):
            spec = _heuristic_spec(profiles, len(records))
            if spec:
                spec["reason"] = spec.get("reason") or "Heuristic fallback (LLM spec missing or invalid)."

        if spec:
            result.add_step(
                action="chart_reason",
                output_summary=f"{spec.get('type')}: {spec.get('reason', '')[:160]}",
            )

        result.status = AgentStatus.SUCCESS
        result.output = {"chart_spec": spec}
        return result

    # ------------------------------------------------------------------
    # LLM call
    # ------------------------------------------------------------------
    def _llm_chart_spec(
        self,
        question: str,
        profiles: Dict[str, Dict[str, Any]],
        records: List[Dict[str, Any]],
    ) -> Optional[Dict[str, Any]]:
        if not self._llm or not self._llm.is_available():
            return None
        try:
            prompt = _CHART_PROMPT.format(
                question=question,
                profile=profile_to_text(profiles),
                sample=json.dumps(records[:5], default=str),
                n_rows=len(records),
                types=", ".join(sorted(SUPPORTED_CHART_TYPES)),
            )
            spec = self._llm.generate_json(prompt, temperature=0.1)
            return spec if isinstance(spec, dict) else None
        except Exception as exc:  # noqa: BLE001
            logger.warning("Visualization LLM failed: %s", exc)
            return None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _find_upstream_records(ctx: AgentContext) -> Optional[Dict[str, Any]]:
    for dep_result in ctx.upstream_results.values():
        if isinstance(dep_result, dict) and "records" in dep_result:
            return dep_result
    return None


def _is_valid_spec(spec: Any, profiles: Dict[str, Dict[str, Any]]) -> bool:
    if not isinstance(spec, dict):
        return False
    if spec.get("type") not in SUPPORTED_CHART_TYPES:
        return False
    x = spec.get("x")
    y = spec.get("y")
    cols = set(profiles.keys())
    if x is not None and x not in cols:
        return False
    # Allow x to be empty for kpi / histogram
    if isinstance(y, str):
        spec["y"] = [y]
        y = spec["y"]
    if isinstance(y, list):
        if not all(c in cols for c in y):
            return False
    elif y is None and spec["type"] not in ("kpi", "histogram", "table"):
        return False
    return True


def _heuristic_spec(
    profiles: Dict[str, Dict[str, Any]],
    n_rows: int,
) -> Optional[Dict[str, Any]]:
    """Typed fallback that uses column profile, not keyword matching."""
    if not profiles:
        return None

    by_type: Dict[str, List[str]] = {"numeric": [], "date": [], "categorical": [], "id": [], "text": []}
    for col, prof in profiles.items():
        by_type.setdefault(prof["dtype"], []).append(col)

    numerics = by_type["numeric"]
    dates = by_type["date"]
    cats = [c for c in by_type["categorical"] if profiles[c]["distinct"] > 0]

    # Single row → KPI
    if n_rows <= 1 and numerics:
        return {
            "type": "kpi",
            "x": None,
            "y": numerics[:1],
            "title": numerics[0].replace("_", " ").title(),
            "reason": "Single-row result is best shown as a headline number.",
        }

    # Date + numeric → line
    if dates and numerics:
        return {
            "type": "multi_line" if len(numerics) > 1 else "line",
            "x": dates[0],
            "y": numerics[:3],
            "title": f"{', '.join(numerics[:2])} over {dates[0]}",
            "reason": "Date column on x-axis with numeric series suggests a trend line.",
        }

    # Two numerics, no usable categorical → scatter
    if len(numerics) >= 2 and not cats:
        return {
            "type": "scatter",
            "x": numerics[0],
            "y": [numerics[1]],
            "title": f"{numerics[1]} vs {numerics[0]}",
            "reason": "Two numeric columns without a categorical axis are best as a scatter plot.",
        }

    # Categorical + numeric → bar (or pie if very few categories)
    if cats and numerics:
        cat = cats[0]
        distinct = profiles[cat]["distinct"]
        if 2 <= distinct <= 6 and n_rows <= 12:
            return {
                "type": "pie",
                "x": cat,
                "y": numerics[:1],
                "title": f"{numerics[0]} share by {cat}",
                "reason": "Few categories make a pie chart readable.",
            }
        return {
            "type": "bar",
            "x": cat,
            "y": numerics[:1],
            "title": f"{numerics[0]} by {cat}",
            "reason": "Categorical x with numeric y is a standard bar comparison.",
        }

    # Only numerics → histogram of the first
    if numerics:
        return {
            "type": "histogram",
            "x": numerics[0],
            "y": None,
            "title": f"Distribution of {numerics[0]}",
            "reason": "Single numeric series with no categorical axis is best shown as a distribution.",
        }

    # Fall back to a table view
    return {
        "type": "table",
        "x": None,
        "y": None,
        "title": "Result",
        "reason": "No obvious numeric/categorical axis pair; show the raw result as a table.",
    }
