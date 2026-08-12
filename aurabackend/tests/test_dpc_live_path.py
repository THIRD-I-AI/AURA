"""DPC verification must actually engage on the LIVE chat DAG.

tests/test_dpc_integration.py already covers the DAGExecutor path, but that is
not the path production chat takes. In the LangGraph orchestrator the
specialists were constructed as bare `cls()` with no tool registry, so
SQLGeneratorAgent's `self.tools` was always None and the DPC cross-check
silently never ran — a well-built safety net wired to nothing.

These tests pin the wiring itself: the node is in the graph, it runs after
execution (never re-executing the user's query), it reports a tri-state
verdict, and it never turns a working answer into a failure.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.langgraph_orchestrator import _dpc_registry, build_graph, verify_node
from agents.schemas import ExecutionOutput, OrchestratorState, SQLGenOutput


@pytest.fixture(autouse=True)
def _enable_chat_dpc(monkeypatch):
    """Inline chat verification is opt-in (AURA_DPC_CHAT_ENABLED, default 0)
    because it costs an LLM round-trip and up to 10s per query. These tests
    exercise the node itself, so they turn it on explicitly."""
    monkeypatch.setenv("AURA_DPC_CHAT_ENABLED", "1")
    yield


def _state(**kw) -> OrchestratorState:
    base = dict(
        user_prompt="total amount by region",
        session_id="dpc-test",
        sql=SQLGenOutput(sql="SELECT region, SUM(amount) FROM t GROUP BY region"),
        execution=ExecutionOutput(
            columns=["region", "total"],
            rows=[["North", 30.0], ["South", 12.0]],
            records=[{"region": "North", "total": 30.0},
                     {"region": "South", "total": 12.0}],
            row_count=2,
        ),
    )
    base.update(kw)
    return OrchestratorState(**base)


def test_verification_node_is_wired_into_the_graph():
    """The regression guard: the node must exist in the compiled DAG. Without
    it the cross-check is dead code no matter how correct it is."""
    g = build_graph()
    nodes = set(g.get_graph().nodes)
    assert "verify_run" in nodes, f"verify_run missing from DAG: {sorted(nodes)}"


@pytest.mark.asyncio
async def test_missing_connection_reports_skipped_not_verified():
    """`skipped` must never masquerade as a pass — an unchecked answer that
    displays as verified is worse than one that admits it was not checked."""
    out = await verify_node(_state(metadata={}))
    assert out["verification"].status == "skipped"
    assert out["verification"].verified is not True


@pytest.mark.asyncio
async def test_verifier_failure_never_breaks_a_working_answer():
    """A broken verifier must not cost the user a result that already
    computed. The node swallows verifier errors into a `skipped` verdict."""
    class _Boom:
        def execute(self, *_a, **_k):
            raise RuntimeError("duckdb exploded")

    out = await verify_node(_state(metadata={"duckdb_con": _Boom()}))
    # Either it skipped or it produced a verdict — but it must not raise, and
    # must not claim verification succeeded.
    assert out == {} or out["verification"].verified is not True


@pytest.mark.asyncio
async def test_dpc_registry_reads_the_same_connection_the_answer_came_from():
    """The cross-check must query the SAME DuckDB the answer came from.

    The default registry's execute_sql posts to the execution-sandbox service,
    which cannot see this request's in-process tables — verifying through it
    would compare against different data and manufacture mismatches.
    """
    duckdb = pytest.importorskip("duckdb")
    con = duckdb.connect(":memory:")
    con.execute("CREATE TABLE t (region VARCHAR, amount DOUBLE)")
    con.execute("INSERT INTO t VALUES ('North', 10.0), ('North', 20.0), ('South', 12.0)")

    reg = _dpc_registry(con)
    res = await reg.call("execute_sql", query='SELECT * FROM "t"')

    assert res["columns"] == ["region", "amount"]
    assert len(res["rows"]) == 3, "registry did not read the caller's own tables"
