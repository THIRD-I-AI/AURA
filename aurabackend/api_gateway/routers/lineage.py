"""
Lineage Router
==============
Returns a directed graph of (table → saved_query → dashboard) edges
scoped to the caller's workspace.

Sprint P-2c: table→query edges are now read from the
``gateway_lineage_edges`` materialised cache instead of being computed
by running sqlglot on every saved query's SQL at request time.
The cache is populated once at create_saved_query time and pruned via
FK CASCADE on delete — GET /lineage is now a single DB read per layer.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import Any, Dict, List

from fastapi import APIRouter, Request

from api_gateway.routers.workspaces import DEFAULT_WORKSPACE_ID, current_workspace_id
from shared.logging_config import get_logger

logger = get_logger("aura.api_gateway.lineage")

router = APIRouter(tags=["Lineage"])


@router.get("/lineage")
async def get_lineage(request: Request):
    """Return nodes + edges for the saved-query → table dependency graph,
    scoped to the caller's workspace.

    Layers, left to right:
      * **table** nodes       — every distinct physical table referenced
      * **saved_query** nodes — every saved query in the library
      * **dashboard** nodes   — every dashboard, connected to its tile queries

    Edges always point "downstream": table → query → dashboard.
    """
    from api_gateway import persistence
    from api_gateway.routers.dashboards import _dashboards_lock, _dashboards_store

    wsid = current_workspace_id(request)

    def _in_ws(r: Dict[str, Any]) -> bool:
        return (r.get("workspace_id") or DEFAULT_WORKSPACE_ID) == wsid

    # P-2c: fetch cached edges and saved-query metadata in parallel —
    # both are indexed workspace-scoped SELECTs, O(edges) + O(queries).
    cached_edges, saved_queries = await asyncio.gather(
        persistence.list_lineage_edges(wsid),
        persistence.list_saved_queries(wsid),
    )
    with _dashboards_lock:
        dashboards = [r for r in _dashboards_store if _in_ws(r)]

    # Group cached edges by query id for O(1) table_count lookup.
    tables_by_query: Dict[str, List[str]] = defaultdict(list)
    for e in cached_edges:
        tables_by_query[e["saved_query_id"]].append(e["table_name"])

    tables: Dict[str, Dict[str, Any]] = {}
    query_nodes: List[Dict[str, Any]] = []
    dashboard_nodes: List[Dict[str, Any]] = []
    edges: List[Dict[str, Any]] = []

    for sq in saved_queries:
        query_id = f"q:{sq['id']}"
        ref_tables = tables_by_query[sq["id"]]
        query_nodes.append({
            "id": query_id,
            "type": "saved_query",
            "label": sq["name"],
            "metadata": {
                "sql": sq.get("sql", ""),
                "prompt": sq.get("prompt"),
                "starred": sq.get("starred", False),
                "scheduled": bool(sq.get("schedule")),
                "table_count": len(ref_tables),
            },
        })
        for table in ref_tables:
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
