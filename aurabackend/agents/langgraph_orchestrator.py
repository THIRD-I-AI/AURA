"""
LangGraph DAG Orchestrator
==========================
Wraps the existing AURA specialists (Planner, SQLGenerator, Execution,
Visualization) in a LangGraph ``StateGraph`` with Pydantic-validated state
transitions.

Why this exists alongside ``agents/executor.py``:
  * ``DAGExecutor`` runs an arbitrary planner-emitted DAG with concurrency.
  * This orchestrator runs a fixed, well-known critical-path DAG
    (plan → sql → execute → visualize) with strict per-node contracts and
    deterministic state transitions — the path most chat queries take.

Both can coexist; the chat router can route to either depending on whether
the planner emits a chain that exceeds the canonical 4-stage path.
"""
from __future__ import annotations

import logging
import time
import uuid
from typing import Any, Dict, Literal, Optional

from langgraph.graph import END, START, StateGraph
from pydantic import ValidationError

from agents.base import AgentContext, AgentStatus, BaseAgent
from agents.planner import PlannerAgent
from agents.schemas import (
    AnalysisOutput,
    ChartSpec,
    ExecutionOutput,
    NodeError,
    OrchestratorState,
    PlannerOutput,
    PlanTask,
    SQLGenOutput,
    VisualizationOutput,
)
from agents.specialists.analysis_agent import AnalysisAgent
from agents.specialists.execution_agent import ExecutionAgent
from agents.specialists.sql_generator_agent import SQLGeneratorAgent
from agents.specialists.visualization_agent import VisualizationAgent

logger = logging.getLogger("aura.agents.langgraph")


# Construct a fresh specialist per orchestration step. Caching here would
# trap the *first* test's monkeypatched ``get_llm`` inside ``self.llm`` and
# leak it across tests; ``get_llm()`` already memoises at the provider layer
# so per-call instantiation is just a few dict lookups.

def _agent(cls: type) -> BaseAgent:
    return cls()


# ── Helpers ───────────────────────────────────────────────────────────

def _ctx_for(
    state: OrchestratorState,
    *,
    task_description: str,
    upstream: Optional[Dict[str, Any]] = None,
    extra_metadata: Optional[Dict[str, Any]] = None,
) -> AgentContext:
    return AgentContext(
        user_prompt=state.user_prompt,
        task_description=task_description,
        session_id=state.session_id,
        upstream_results=upstream or {},
        schema_context=dict(state.schema_context),
        connection=dict(state.connection),
        files=list(state.files),
        metadata={**state.metadata, **(extra_metadata or {})},
    )


def _err(state: OrchestratorState, node: str, msg: str, dur_ms: float) -> Dict[str, Any]:
    return {"errors": state.errors + [NodeError(node=node, message=msg, duration_ms=dur_ms)]}


def _completed(state: OrchestratorState, node: str) -> list[str]:
    return state.completed_nodes + [node]


# ── Nodes ─────────────────────────────────────────────────────────────

async def planner_node(state: OrchestratorState) -> Dict[str, Any]:
    t0 = time.perf_counter()
    res = await _agent(PlannerAgent).execute(
        _ctx_for(state, task_description=state.user_prompt)
    )
    dur = (time.perf_counter() - t0) * 1000

    if res.status == AgentStatus.FAILED:
        return _err(state, "planner", res.error or "planner failed", dur)

    try:
        plan = PlannerOutput(
            plan_id=res.output.get("plan_id", state.session_id),
            summary=res.output.get("summary", ""),
            estimated_duration_sec=int(res.output.get("estimated_duration_sec", 0) or 0),
            tasks=[PlanTask(**t) for t in res.output.get("tasks", [])],
        )
    except ValidationError as exc:
        return _err(state, "planner", f"plan schema invalid: {exc.errors()}", dur)

    return {"plan": plan, "completed_nodes": _completed(state, "planner")}


async def sql_gen_node(state: OrchestratorState) -> Dict[str, Any]:
    t0 = time.perf_counter()
    sql_task = next(
        (t for t in (state.plan.tasks if state.plan else []) if t.agent_name == "SQLGeneratorAgent"),
        None,
    )
    description = sql_task.description if sql_task else state.user_prompt

    res = await _agent(SQLGeneratorAgent).execute(
        _ctx_for(state, task_description=description)
    )
    dur = (time.perf_counter() - t0) * 1000

    if res.status == AgentStatus.FAILED:
        return _err(state, "sql_gen", res.error or "sql gen failed", dur)

    sql_text = (res.output.get("sql") or "").strip()
    if not sql_text:
        return _err(state, "sql_gen", "empty SQL output from generator", dur)

    try:
        sql_out = SQLGenOutput(
            sql=sql_text,
            explanation=res.output.get("explanation"),
            dialect=res.output.get("dialect", "duckdb"),
        )
    except ValidationError as exc:
        return _err(state, "sql_gen", f"sql schema invalid: {exc.errors()}", dur)

    return {"sql": sql_out, "completed_nodes": _completed(state, "sql_gen")}


async def execution_node(state: OrchestratorState) -> Dict[str, Any]:
    t0 = time.perf_counter()
    if state.sql is None:
        return _err(state, "execution", "no SQL to execute", 0.0)

    # ExecutionAgent reads SQL out of upstream_results — it accepts either a
    # raw string or a {"sql": ...} dict. Pass the dict shape so the agent's
    # markdown-fence stripping does not fire on already-clean SQL.
    res = await _agent(ExecutionAgent).execute(
        _ctx_for(
            state,
            task_description="Execute generated SQL",
            upstream={"sql_gen": {"sql": state.sql.sql}},
        )
    )
    dur = (time.perf_counter() - t0) * 1000

    if res.status == AgentStatus.FAILED:
        return _err(state, "execution", res.error or "execution failed", dur)

    try:
        records = list(res.output.get("records", []))
        out = ExecutionOutput(
            columns=list(res.output.get("columns", [])),
            records=records,
            rows=list(res.output.get("rows", [])),
            row_count=len(records),
            truncated=bool(res.output.get("truncated", False)),
        )
    except ValidationError as exc:
        return _err(state, "execution", f"execution schema invalid: {exc.errors()}", dur)

    return {"execution": out, "completed_nodes": _completed(state, "execution")}


async def visualization_node(state: OrchestratorState) -> Dict[str, Any]:
    t0 = time.perf_counter()
    if state.execution is None or state.execution.row_count == 0:
        viz = VisualizationOutput(skipped_reason="no rows to visualize")
        return {"visualization": viz, "completed_nodes": _completed(state, "visualization")}

    # BAVT pivot: if the live BATS tracker can't afford this node's
    # projected cost, skip rather than risk a hard context cutoff.
    from shared.bavt import can_afford
    if can_afford("viz_run") is False:
        viz = VisualizationOutput(skipped_reason="BAVT pivot: insufficient token budget")
        return {"visualization": viz, "completed_nodes": _completed(state, "visualization")}

    res = await _agent(VisualizationAgent).execute(
        _ctx_for(
            state,
            task_description=state.user_prompt,
            upstream={"execution": {
                "records": state.execution.records,
                "columns": state.execution.columns,
                "rows": state.execution.rows,
            }},
        )
    )
    dur = (time.perf_counter() - t0) * 1000

    if res.status == AgentStatus.FAILED:
        return _err(state, "visualization", res.error or "visualization failed", dur)

    chart_payload = res.output.get("chart") or res.output.get("chartSpec") or res.output
    if not isinstance(chart_payload, dict):
        return _err(state, "visualization", "viz output is not a dict", dur)

    try:
        chart = ChartSpec(
            type=chart_payload.get("type", "table"),
            x=chart_payload.get("x"),
            y=list(chart_payload.get("y") or []),
            title=chart_payload.get("title", ""),
            reason=chart_payload.get("reason", ""),
        )
    except ValidationError as exc:
        return _err(state, "visualization", f"chart schema invalid: {exc.errors()}", dur)

    viz = VisualizationOutput(chart=chart)
    return {"visualization": viz, "completed_nodes": _completed(state, "visualization")}


async def analysis_node(state: OrchestratorState) -> Dict[str, Any]:
    """Synthesise a narrative answer from rows + SQL + chart. Skipped on empty results."""
    t0 = time.perf_counter()
    if state.execution is None or state.execution.row_count == 0:
        out = AnalysisOutput(skipped_reason="no rows to analyse")
        return {"analysis": out, "completed_nodes": _completed(state, "analysis")}

    # BAVT pivot: drop the narrative synthesis step when the budget is
    # too tight — the user still has SQL + raw rows + chart from
    # upstream nodes, and surfacing a structured "skipped" beats a
    # crashed run from a context-window blow-up.
    from shared.bavt import can_afford
    if can_afford("analysis_run") is False:
        out = AnalysisOutput(skipped_reason="BAVT pivot: insufficient token budget")
        return {"analysis": out, "completed_nodes": _completed(state, "analysis")}

    # AnalysisAgent reads ``records / columns / sql / chart_spec`` out of
    # upstream_results — assemble the same shape from typed state.
    chart_dict: Optional[Dict[str, Any]] = None
    if state.visualization and state.visualization.chart:
        chart_dict = state.visualization.chart.model_dump()

    upstream: Dict[str, Any] = {
        "execution": {
            "records": state.execution.records,
            "columns": state.execution.columns,
        },
        "sql_gen": {"sql": state.sql.sql if state.sql else None},
    }
    if chart_dict is not None:
        upstream["visualization"] = {"chart_spec": chart_dict}

    res = await _agent(AnalysisAgent).execute(
        _ctx_for(state, task_description=state.user_prompt, upstream=upstream)
    )
    dur = (time.perf_counter() - t0) * 1000

    if res.status == AgentStatus.FAILED:
        return _err(state, "analysis", res.error or "analysis failed", dur)

    try:
        out = AnalysisOutput(
            conclusion=res.output.get("conclusion"),
            stats=dict(res.output.get("stats") or {}),
        )
    except ValidationError as exc:
        return _err(state, "analysis", f"analysis schema invalid: {exc.errors()}", dur)

    return {"analysis": out, "completed_nodes": _completed(state, "analysis")}


# ── Routing ───────────────────────────────────────────────────────────

def _last_error_node(state: OrchestratorState) -> Optional[str]:
    return state.errors[-1].node if state.errors else None


# NOTE on node naming: LangGraph rejects node IDs that collide with state
# field names (because reducers use the same key namespace). Our state has
# ``plan``, ``sql``, ``execution``, ``visualization``, ``analysis`` slots,
# so node IDs use the *_run suffix instead.

def _route_after_planner(state: OrchestratorState) -> Literal["sql_run", "end"]:
    if _last_error_node(state) == "planner":
        return "end"
    if state.plan and state.plan.has_sql_path:
        return "sql_run"
    return "end"


def _route_after_sql_gen(state: OrchestratorState) -> Literal["exec_run", "end"]:
    if _last_error_node(state) == "sql_gen" or state.sql is None:
        return "end"
    return "exec_run"


def _route_after_execution(state: OrchestratorState) -> Literal["viz_run", "end"]:
    if _last_error_node(state) == "execution" or state.execution is None:
        return "end"
    return "viz_run"


def _route_after_visualization(state: OrchestratorState) -> Literal["analysis_run", "end"]:
    # Visualization failure shouldn't cancel analysis — a missing chart
    # still leaves rows worth narrating. Only skip analysis when the
    # chat caller explicitly opted out.
    if state.metadata.get("skip_analysis"):
        return "end"
    return "analysis_run"


def _entry_point(state: OrchestratorState) -> Literal["planner", "sql_run"]:
    """Conditional START router: chat passes ``skip_planner=True`` because
    it has already decided the canonical NL→SQL→Execute→Viz→Analyse path."""
    if state.metadata.get("skip_planner"):
        return "sql_run"
    return "planner"


# ── Graph builder ─────────────────────────────────────────────────────

def build_graph():
    g = StateGraph(OrchestratorState)
    g.add_node("planner", planner_node)
    g.add_node("sql_run", sql_gen_node)
    g.add_node("exec_run", execution_node)
    g.add_node("viz_run", visualization_node)
    g.add_node("analysis_run", analysis_node)

    # Conditional entry — Planner-led runs vs. chat's explicit-plan path.
    g.add_conditional_edges(
        START, _entry_point,
        {"planner": "planner", "sql_run": "sql_run"},
    )
    g.add_conditional_edges(
        "planner", _route_after_planner,
        {"sql_run": "sql_run", "end": END},
    )
    g.add_conditional_edges(
        "sql_run", _route_after_sql_gen,
        {"exec_run": "exec_run", "end": END},
    )
    g.add_conditional_edges(
        "exec_run", _route_after_execution,
        {"viz_run": "viz_run", "end": END},
    )
    g.add_conditional_edges(
        "viz_run", _route_after_visualization,
        {"analysis_run": "analysis_run", "end": END},
    )
    g.add_edge("analysis_run", END)

    return g.compile()


_GRAPH = None


def get_graph():
    global _GRAPH
    if _GRAPH is None:
        _GRAPH = build_graph()
    return _GRAPH


async def run_orchestrator(
    user_prompt: str,
    *,
    session_id: Optional[str] = None,
    files: Optional[list[str]] = None,
    connection: Optional[Dict[str, Any]] = None,
    schema_context: Optional[Dict[str, Any]] = None,
    metadata: Optional[Dict[str, Any]] = None,
    token_budget: Optional[int] = None,
    tool_call_budget: Optional[int] = None,
    wall_seconds: Optional[float] = None,
    skip_planner: bool = False,
    skip_analysis: bool = False,
    duckdb_con: Any = None,
) -> OrchestratorState:
    """One-shot driver. Returns the final validated ``OrchestratorState``.

    When any of ``token_budget`` / ``tool_call_budget`` / ``wall_seconds`` is
    supplied, BATS is enabled for this run: a ``BudgetTracker`` is bound to
    the current async context, every LLM call debits tokens automatically,
    every node debits one tool-call, and a depleted budget fails the next
    agent — which the conditional edges route straight to END.
    """
    sid = session_id or uuid.uuid4().hex[:12]
    merged_metadata: Dict[str, Any] = {**(metadata or {})}
    # Routing flags + the DuckDB connection ride on metadata so they
    # flow into every node's AgentContext via _ctx_for without expanding
    # the OrchestratorState surface area for every new option.
    if skip_planner:
        merged_metadata["skip_planner"] = True
    if skip_analysis:
        merged_metadata["skip_analysis"] = True
    if duckdb_con is not None:
        merged_metadata["duckdb_con"] = duckdb_con

    initial = OrchestratorState(
        user_prompt=user_prompt,
        session_id=sid,
        files=files or [],
        connection=connection or {},
        schema_context=schema_context or {},
        metadata=merged_metadata,
    )

    bats_enabled = any(v is not None for v in (token_budget, tool_call_budget, wall_seconds))
    token = None
    if bats_enabled:
        from shared.budget import BudgetTracker, set_current_budget
        tracker_kwargs: Dict[str, Any] = {"session_id": sid}
        if token_budget is not None:
            tracker_kwargs["token_budget"] = token_budget
        if tool_call_budget is not None:
            tracker_kwargs["tool_call_budget"] = tool_call_budget
        if wall_seconds is not None:
            tracker_kwargs["wall_seconds"] = wall_seconds
        token = set_current_budget(BudgetTracker(**tracker_kwargs))

    try:
        final = await get_graph().ainvoke(initial)
    finally:
        if token is not None:
            from shared.budget import reset_current_budget
            reset_current_budget(token)

    # ainvoke returns a state dict (or the model, depending on langgraph
    # version) — re-validate so callers always get a typed instance.
    return OrchestratorState.model_validate(final)
