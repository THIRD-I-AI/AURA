"""
DAR LangGraph DAG
==================
Six nodes, fixed order, no Planner — DAR's research path is canonical.

  introspect  → pull column list + dtypes from DuckDB
  profile     → compute null_rate / mean / std / min / max / top_values
  formulate   → DARResearchAgent emits 3-5 (question, sql) pairs
  execute     → run each SQL against DuckDB (read-only), capture rows
  score       → DARResearchAgent classifies + scores each result
  persist     → write Finding rows to metadata DB (dar_insights)

Every step that touches DuckDB opens a fresh read-only connection —
the worker never holds an exclusive lock on the analytics lake while
the UASR MAPE-K worker is writing into it.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from typing import Any, Dict, List, Literal, Optional

from langgraph.graph import END, START, StateGraph
from pydantic import ValidationError

from agents.base import AgentContext, AgentStatus
from agents.specialists.dar_research_agent import DARResearchAgent

from .schemas import (
    ColumnProfile,
    DARState,
    Finding,
    NodeError,
    QueryResult,
    ResearchQuestion,
)

logger = logging.getLogger("aura.dar.graph")


# ── Helpers ───────────────────────────────────────────────────────────

_RESEARCH_AGENT: Optional[DARResearchAgent] = None


def _agent() -> DARResearchAgent:
    global _RESEARCH_AGENT
    if _RESEARCH_AGENT is None:
        _RESEARCH_AGENT = DARResearchAgent()
    return _RESEARCH_AGENT


def _err(state: DARState, node: str, msg: str, dur_ms: float = 0.0) -> Dict[str, Any]:
    return {"errors": state.errors + [NodeError(node=node, message=msg, duration_ms=dur_ms)]}


def _completed(state: DARState, node: str) -> List[str]:
    return state.completed_nodes + [node]


def _open_duckdb(path: str) -> Any:
    import duckdb
    return duckdb.connect(path, read_only=True)


# ── Nodes ─────────────────────────────────────────────────────────────

def introspect_node(state: DARState) -> Dict[str, Any]:
    """Sync node — DuckDB introspection. Wrapped in to_thread by the
    LangGraph runtime when invoked through ainvoke."""
    t0 = time.perf_counter()
    try:
        con = _open_duckdb(state.duckdb_path)
        try:
            descr = con.execute(f'DESCRIBE "{state.table_name}"').fetchall()
        finally:
            con.close()
    except Exception as exc:
        return _err(state, "introspect", f"DuckDB DESCRIBE failed: {exc}",
                    (time.perf_counter() - t0) * 1000)

    cols: List[ColumnProfile] = []
    for row in descr:
        try:
            cols.append(ColumnProfile(
                column=row[0],
                data_type=str(row[1]),
                null_rate=0.0,
            ))
        except ValidationError as exc:
            return _err(state, "introspect", f"column schema invalid: {exc.errors()}")
    if not cols:
        return _err(state, "introspect", f"table {state.table_name} has no columns")
    return {"schema_columns": cols, "completed_nodes": _completed(state, "introspect")}


def profile_node(state: DARState) -> Dict[str, Any]:
    """Compute distribution stats per column. Numeric → mean/std/min/max,
    categorical → top-3 values + counts. One-pass query per column."""
    t0 = time.perf_counter()
    try:
        con = _open_duckdb(state.duckdb_path)
    except Exception as exc:
        return _err(state, "profile", f"DuckDB open failed: {exc}")

    profiled: List[ColumnProfile] = []
    try:
        try:
            total = con.execute(
                f'SELECT COUNT(*) FROM "{state.table_name}"'
            ).fetchone()[0]
        except Exception as exc:
            return _err(state, "profile", f"row count failed: {exc}",
                        (time.perf_counter() - t0) * 1000)
        if not total:
            return _err(state, "profile", f"table {state.table_name} is empty")

        for col in state.schema_columns:
            ident = f'"{state.table_name}"."{col.column}"'
            try:
                null_count = con.execute(
                    f'SELECT COUNT(*) FROM "{state.table_name}" WHERE {ident} IS NULL'
                ).fetchone()[0]
                col_null_rate = float(null_count) / max(total, 1)
            except Exception:
                col_null_rate = 0.0

            distinct: Optional[int] = None
            try:
                distinct = con.execute(
                    f'SELECT COUNT(DISTINCT {ident}) FROM "{state.table_name}"'
                ).fetchone()[0]
            except Exception:
                pass

            updated = col.model_copy(update={
                "null_rate": col_null_rate,
                "distinct_count": distinct,
            })

            dtype_lower = col.data_type.lower()
            if any(t in dtype_lower for t in ("int", "double", "float", "decimal", "numeric")):
                try:
                    stats = con.execute(
                        f'SELECT AVG({ident}), STDDEV({ident}), '
                        f'MIN({ident}), MAX({ident}) '
                        f'FROM "{state.table_name}"'
                    ).fetchone()
                    updated = updated.model_copy(update={
                        "mean": _safe_float(stats[0]),
                        "std": _safe_float(stats[1]),
                        "min": _coerce(stats[2]),
                        "max": _coerce(stats[3]),
                    })
                except Exception:
                    pass
            else:
                # Categorical / temporal — top-3 values
                try:
                    rows = con.execute(
                        f'SELECT {ident} AS v, COUNT(*) AS n '
                        f'FROM "{state.table_name}" '
                        f'WHERE {ident} IS NOT NULL '
                        f'GROUP BY {ident} ORDER BY n DESC LIMIT 3'
                    ).fetchall()
                    updated = updated.model_copy(update={
                        "top_values": [{"value": _coerce(r[0]), "count": int(r[1])} for r in rows],
                    })
                except Exception:
                    pass
            profiled.append(updated)
    finally:
        con.close()

    profile_text = _render_profile(state.table_name, profiled, total)
    return {
        "schema_columns": profiled,
        "profile_text": profile_text,
        "completed_nodes": _completed(state, "profile"),
    }


async def formulate_node(state: DARState) -> Dict[str, Any]:
    t0 = time.perf_counter()
    res = await _agent().execute(AgentContext(
        user_prompt="(headless DAR research)",
        task_description=f"Formulate research questions for {state.table_name}",
        session_id=state.run_id,
        metadata={
            "dar_mode": "formulate",
            "table_name": state.table_name,
            "profile_text": state.profile_text,
        },
    ))
    dur = (time.perf_counter() - t0) * 1000
    if res.status == AgentStatus.FAILED:
        return _err(state, "formulate", res.error or "formulate failed", dur)

    questions: List[ResearchQuestion] = []
    for q in res.output.get("questions", []):
        try:
            questions.append(ResearchQuestion(question=q["question"], sql=q["sql"]))
        except (KeyError, ValidationError):
            continue
    if not questions:
        return _err(state, "formulate", "no valid questions returned", dur)
    return {"questions": questions, "completed_nodes": _completed(state, "formulate")}


def execute_node(state: DARState) -> Dict[str, Any]:
    """Run each question's SQL against DuckDB. read_only=True at the
    connection level enforces SELECT-only — destructive statements
    fail at the engine boundary, no extra parsing required."""
    t0 = time.perf_counter()
    try:
        con = _open_duckdb(state.duckdb_path)
    except Exception as exc:
        return _err(state, "execute", f"DuckDB open failed: {exc}",
                    (time.perf_counter() - t0) * 1000)

    results: List[QueryResult] = []
    try:
        for q in state.questions:
            try:
                cur = con.execute(q.sql)
                cols = [d[0] for d in cur.description]
                rows = [[_coerce(c) for c in r] for r in cur.fetchall()]
                results.append(QueryResult(
                    question=q.question, sql=q.sql,
                    columns=cols, rows=rows, row_count=len(rows),
                ))
            except Exception as exc:
                # Per-question failure isn't fatal — record and continue.
                results.append(QueryResult(
                    question=q.question, sql=q.sql,
                    columns=[], rows=[], row_count=0, error=str(exc),
                ))
    finally:
        con.close()

    return {"query_results": results, "completed_nodes": _completed(state, "execute")}


async def score_node(state: DARState) -> Dict[str, Any]:
    """One LLM call per query result — small fan-out, max 5 questions
    by formulate's contract. Sequential rather than parallel because
    rate limits + caching mean parallel rarely speeds this up."""

    findings: List[Finding] = []
    for qr in state.query_results:
        if qr.error or qr.row_count == 0:
            findings.append(Finding(
                question=qr.question, sql=qr.sql,
                finding_type="summary",
                summary=(qr.error or "no rows returned"),
                score=0.05, is_anomaly=False,
                payload={"error": qr.error, "row_count": qr.row_count},
            ))
            continue

        rows_as_dicts = [dict(zip(qr.columns, r)) for r in qr.rows]
        res = await _agent().execute(AgentContext(
            user_prompt="(headless DAR scoring)",
            task_description=f"Score the result for: {qr.question}",
            session_id=state.run_id,
            metadata={
                "dar_mode": "score",
                "question": qr.question,
                "sql": qr.sql,
                "rows": rows_as_dicts,
            },
        ))
        if res.status == AgentStatus.FAILED:
            findings.append(Finding(
                question=qr.question, sql=qr.sql,
                finding_type="summary",
                summary=f"scoring failed: {res.error or 'unknown'}",
                score=0.0, is_anomaly=False,
                payload={"row_count": qr.row_count, "columns": qr.columns},
            ))
            continue
        try:
            findings.append(Finding(
                question=qr.question,
                sql=qr.sql,
                finding_type=res.output.get("finding_type", "summary"),
                summary=res.output.get("summary", ""),
                score=float(res.output.get("score", 0.0)),
                is_anomaly=bool(res.output.get("is_anomaly", False)),
                payload={
                    "columns": qr.columns,
                    "rows": qr.rows[:50],  # cap stored rows
                    "row_count": qr.row_count,
                },
            ))
        except ValidationError as exc:
            findings.append(Finding(
                question=qr.question, sql=qr.sql,
                finding_type="summary",
                summary=f"scoring schema invalid: {exc.errors()[:1]}",
                score=0.0, is_anomaly=False,
                payload={"row_count": qr.row_count},
            ))

    return {"findings": findings, "completed_nodes": _completed(state, "score")}


async def persist_node(state: DARState) -> Dict[str, Any]:
    """Write findings to metadata DB. Each finding gets a fresh UUID;
    the run_id is the same for all findings in this DAR run so the UI
    can group them."""
    t0 = time.perf_counter()
    if not state.findings:
        return {"completed_nodes": _completed(state, "persist")}

    from datetime import datetime, timezone

    from sqlalchemy.ext.asyncio import AsyncSession

    from metadata_store.db import get_session_factory
    from metadata_store.models import DARInsight

    persisted: List[str] = []
    try:
        factory = get_session_factory()
        async with factory() as sess:  # type: AsyncSession
            for f in state.findings:
                row = DARInsight(
                    id=uuid.uuid4().hex[:24],
                    source_id=state.source_id,
                    table_name=state.table_name,
                    question=f.question,
                    sql_query=f.sql,
                    finding_type=f.finding_type,
                    summary=f.summary,
                    score=f.score,
                    is_anomaly=f.is_anomaly,
                    payload=f.payload,
                    run_id=state.run_id,
                    created_at=datetime.now(timezone.utc),
                )
                sess.add(row)
                persisted.append(row.id)
            await sess.commit()
    except Exception as exc:
        return _err(state, "persist", f"persist failed: {exc}",
                    (time.perf_counter() - t0) * 1000)

    return {"persisted_ids": persisted, "completed_nodes": _completed(state, "persist")}


# ── Routing ───────────────────────────────────────────────────────────

def _route_after(node: str, next_step: str):
    def router(state: DARState) -> Literal["next", "end"]:
        last = state.errors[-1].node if state.errors else None
        return "end" if last == node else "next"  # type: ignore[return-value]
    router.__name__ = f"_route_after_{node}"
    return router


# ── Helpers ───────────────────────────────────────────────────────────

def _safe_float(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        f = float(v)
        if f != f:  # NaN
            return None
        return f
    except (TypeError, ValueError):
        return None


def _coerce(v: Any) -> Any:
    """Coerce DuckDB return values into JSON-safe Python types."""
    if v is None or isinstance(v, (str, int, float, bool)):
        return v
    if hasattr(v, "isoformat"):
        return v.isoformat()
    return str(v)


def _render_profile(table: str, cols: List[ColumnProfile], total_rows: int) -> str:
    """Compact human-readable text for the formulate prompt — keeps
    LLM token cost predictable regardless of column count."""
    lines = [f'Table "{table}" — {total_rows} rows, {len(cols)} columns:']
    for c in cols:
        bits = [f'  {c.column} ({c.data_type})']
        bits.append(f'null={c.null_rate:.1%}')
        if c.distinct_count is not None:
            bits.append(f'distinct={c.distinct_count}')
        if c.mean is not None:
            bits.append(f'mean={c.mean:.4g} std={c.std or 0.0:.4g}')
            bits.append(f'range=[{c.min}, {c.max}]')
        if c.top_values:
            tv = ", ".join(f'{t["value"]}({t["count"]})' for t in c.top_values[:3])
            bits.append(f'top=[{tv}]')
        lines.append(" ".join(bits))
    return "\n".join(lines)


# ── Graph builder ─────────────────────────────────────────────────────

def build_graph():
    g = StateGraph(DARState)
    g.add_node("introspect", introspect_node)
    g.add_node("profile", profile_node)
    g.add_node("formulate", formulate_node)
    g.add_node("execute", execute_node)
    g.add_node("score", score_node)
    g.add_node("persist", persist_node)

    g.add_edge(START, "introspect")
    g.add_conditional_edges("introspect", _route_after("introspect", "profile"),
                            {"next": "profile", "end": END})
    g.add_conditional_edges("profile", _route_after("profile", "formulate"),
                            {"next": "formulate", "end": END})
    g.add_conditional_edges("formulate", _route_after("formulate", "execute"),
                            {"next": "execute", "end": END})
    g.add_conditional_edges("execute", _route_after("execute", "score"),
                            {"next": "score", "end": END})
    g.add_conditional_edges("score", _route_after("score", "persist"),
                            {"next": "persist", "end": END})
    g.add_edge("persist", END)

    return g.compile()


_GRAPH = None


def get_graph():
    global _GRAPH
    if _GRAPH is None:
        _GRAPH = build_graph()
    return _GRAPH


async def run_dar(
    source_id: str,
    table_name: str,
    *,
    duckdb_path: str,
    run_id: Optional[str] = None,
) -> DARState:
    """One-shot driver. Returns the final validated DARState."""
    initial = DARState(
        run_id=run_id or uuid.uuid4().hex[:16],
        source_id=source_id,
        table_name=table_name,
        duckdb_path=duckdb_path,
    )
    final = await get_graph().ainvoke(initial)
    return DARState.model_validate(final)
