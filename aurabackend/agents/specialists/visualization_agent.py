from typing import Any

from agents.base import AgentContext, AgentResult, AgentStatus, BaseAgent


class VisualizationAgent(BaseAgent):
    """
    Analyzes the tabular records produced by the ExecutionAgent and outputs an optimal JSON 
    configuration spec for Recharts front-end rendering.
    """

    name = "VisualizationAgent"
    description = "Suggests appropriate charts based on query output."

    async def _run(self, ctx: AgentContext, result: AgentResult) -> AgentResult:
        # Get data from upstream ExecutionAgent
        upstream_data = None
        for dep_result in ctx.upstream_results.values():
            if isinstance(dep_result, dict) and "records" in dep_result:
                upstream_data = dep_result
                break

        if not upstream_data or not upstream_data.get("records"):
            result.status = AgentStatus.SUCCESS
            result.output = {"chart_spec": None}
            return result

        records = upstream_data["records"]
        columns = upstream_data["columns"]

        # Determine optimal chart configuration
        def _suggest_chart(user_query, cols, data):
            if not data or not cols: return None
            q = user_query.lower()
            num_cols = [c for c in cols if data and isinstance(data[0].get(c), (int, float))]
            str_cols = [c for c in cols if c not in num_cols]

            if any(w in q for w in ["trend", "over time", "monthly", "daily", "weekly", "yearly"]):
                return {"type": "line", "x": str_cols[0] if str_cols else cols[0], "y": num_cols[0] if num_cols else cols[-1], "title": "Trend"}
            if any(w in q for w in ["top", "compare", "group", "breakdown", "versus", "vs"]):
               return {"type": "bar", "x": str_cols[0] if str_cols else cols[0], "y": num_cols[0] if num_cols else cols[-1], "title": "Comparison"}
            if any(w in q for w in ["distribution", "share", "percentage", "proportion", "pie"]):
               return {"type": "pie", "x": str_cols[0] if str_cols else cols[0], "y": num_cols[0] if num_cols else cols[-1], "title": "Distribution"}

            if str_cols and num_cols:
                return {"type": "bar", "x": str_cols[0], "y": num_cols[0], "title": "Data Visualization"}
            return None

        chart_spec = _suggest_chart(ctx.user_prompt, columns, records)

        result.status = AgentStatus.SUCCESS
        result.output = {"chart_spec": chart_spec}
        result.add_step(action="chart_suggestion", output_summary=str(chart_spec))

        return result
