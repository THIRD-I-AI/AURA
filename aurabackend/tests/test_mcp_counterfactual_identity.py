"""BUG-422: the MCP counterfactual tools must call the service with a real identity."""
from __future__ import annotations

import pytest

pytest.importorskip("mcp")

from tests._synthetic_data import synthetic_dag_full  # noqa: E402


@pytest.mark.asyncio
async def test_a_job_submitted_over_mcp_can_be_read_back(monkeypatch):
    import counterfactual_service.main as cf_main
    from mcp_servers.aura_mcp_server import build_server

    async def _no_run(job_id, query):
        return None

    monkeypatch.setattr(cf_main, "_run_async", _no_run)
    tools = build_server()._tool_manager
    query = {
        "question": "mcp",
        "treatment": {"column": "treatment", "actual": 1.0, "counterfactual": 0.0},
        "outcome": {"column": "outcome", "agg": "sum", "window": ["2025-01-01", "2025-12-31"]},
        "dag": {"edges": synthetic_dag_full()["edges"]},
        "dataset": {"source_id": "mcp"},
    }

    submitted = await tools.get_tool("counterfactual_run").fn(query=query)
    fetched = await tools.get_tool("counterfactual_get").fn(job_id=submitted["job_id"])

    assert fetched["job_id"] == submitted["job_id"]
    assert fetched["state"] in {"queued", "running", "succeeded"}
