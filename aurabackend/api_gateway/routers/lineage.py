"""
Lineage Router
==============
Walks every saved query via sqlglot, extracts referenced tables, and
returns a directed graph of (table → saved_query) edges plus a second
layer of (saved_query → dashboard) edges.

Graph is computed on every request — saved queries & dashboards both
live in in-memory stores, so this is cheap even for a few hundred
entries.
"""

from __future__ import annotations

from typing import Any, Dict, List, Set, Tuple

from fastapi import APIRouter, Request

from api_gateway.routers.workspaces import DEFAULT_WORKSPACE_ID, current_workspace_id
from shared.logging_config import get_logger

logger = get_logger("aura.api_gateway.lineage")

router = APIRouter(tags=["Lineage"])


def _extract_tables(sql: str) -> Set[str]:
    """Return the set of table names referenced in the SQL. Best effort."""
    try:
        import sqlglot
        from sqlglot import exp
    except ImportError:  # pragma: no cover — sqlglot is a hard dep after S2.4
        return set()
    try:
        parsed = sqlglot.parse(sql, error_level="ignore")
    except Exception:
        return set()
    tables: Set[str] = set()
    for statement in parsed or []:
        if statement is None:
            continue
        for table in statement.find_all(exp.Table):
            name = getattr(table, "name", None)
            if name and not name.startswith("__"):
                tables.add(name.lower())
    return tables


@router.get("/lineage")
async def get_lineage(request: Request):
    """Return nodes + edges for the saved-query → table dependency graph,
    scoped to the caller's workspace.

    Layers, left to right:
      * **table** nodes      — every distinct physical table referenced
      * **saved_query** nodes — every saved query in the library
      * **dashboard** nodes   — every dashboard, connected to its tile queries

    Edges always point "downstream": table → query → dashboard.
    """
    from api_gateway.routers.dashboards import _dashboards_lock, _dashboards_store
    from api_gateway.routers.queries import _saved_queries_lock, _saved_queries_store

    wsid = current_workspace_id(request)

    def _in_ws(r: Dict[str, Any]) -> bool:
        return (r.get("workspace_id") or DEFAULT_WORKSPACE_ID) == wsid

    with _saved_queries_lock:
        saved_queries = [r for r in _saved_queries_store if _in_ws(r)]
    with _dashboards_lock:
        dashboards = [r for r in _dashboards_store if _in_ws(r)]

    tables: Dict[str, Dict[str, Any]] = {}
    query_nodes: List[Dict[str, Any]] = []
    dashboard_nodes: List[Dict[str, Any]] = []
    edges: List[Dict[str, Any]] = []

    for sq in saved_queries:
        refs = _extract_tables(sq.get("sql", ""))
        query_id = f"q:{sq['id']}"
        query_nodes.append({
            "id": query_id,
            "type": "saved_query",
            "label": sq["name"],
            "metadata": {
                "sql": sq.get("sql", ""),
                "prompt": sq.get("prompt"),
                "starred": sq.get("starred", False),
                "scheduled": bool(sq.get("schedule")),
                "table_count": len(refs),
            },
        })
        for table in refs:
            table_id = f"t:{table}"
            tables.setdefault(table_id, {
                "id": table_id,
                "type": "table",
                "label": table,
                "metadata": {"referenced_by": 0},
            })
            tables[table_id]["metadata"]["referenced_by"] += 1
            edges.append({
                "id": f"{table_id}->{query_id}",
                "source": table_id,
                "target": query_id,
            })

    # saved_query → dashboard edges
    for dash in dashboards:
        dash_id = f"d:{dash['id']}"
        tile_queries = [t.get("saved_query_id") for t in dash.get("tiles", [])]
        dashboard_nodes.append({
            "id": dash_id,
            "type": "dashboard",
            "label": dash["name"],
            "metadata": {
                "tile_count": len(tile_queries),
                "description": dash.get("description"),
            },
        })
        for sq_id in tile_queries:
            src = f"q:{sq_id}"
            edges.append({
                "id": f"{src}->{dash_id}",
                "source": src,
                "target": dash_id,
            })

    return {
        "success": True,
        "nodes": list(tables.values()) + query_nodes + dashboard_nodes,
        "edges": edges,
        "summary": {
            "tables": len(tables),
            "queries": len(query_nodes),
            "dashboards": len(dashboard_nodes),
            "edges": len(edges),
        },
    }
