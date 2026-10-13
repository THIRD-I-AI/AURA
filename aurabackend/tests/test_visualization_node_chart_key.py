"""BUG-433: the visualization node keeps the chart the agent chose."""
from __future__ import annotations

import pytest

import agents.langgraph_orchestrator as orch
from agents.base import AgentResult, AgentStatus
from agents.specialists.visualization_agent import VisualizationAgent


@pytest.mark.asyncio
async def test_the_agents_chart_spec_survives_the_node(monkeypatch):
    records = [{"region": "north", "total": 10}, {"region": "south", "total": 7}]
    spec = {"type": "bar", "x": "region", "y": ["total"], "title": "Sales by region", "reason": "categorical x"}

    async def _run(self, ctx, result: AgentResult) -> AgentResult:
        result.status = AgentStatus.SUCCESS
        result.output = {"chart_spec": spec}  # the shape VisualizationAgent really writes
        return result

    monkeypatch.setattr(VisualizationAgent, "_run", _run)
    state = orch.OrchestratorState(
        session_id="s1", user_prompt="total sales by region",
        execution=orch.ExecutionOutput(records=records, columns=["region", "total"],
                                       rows=[["north", 10], ["south", 7]], row_count=2),
    )

    out = await orch.visualization_node(state)

    chart = out["visualization"].chart
    assert (chart.type, chart.x, chart.y, chart.title) == ("bar", "region", ["total"], "Sales by region")
