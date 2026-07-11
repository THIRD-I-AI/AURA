"""
Connections Router
===================
Database connection CRUD, testing, schema introspection, and connector proxies.
"""

import os
import threading
import uuid as _uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from connectors import (
    ConnectorConfig,
    SourceType,
    available_connectors,
    build_connector,
    get_connector,
)
from shared.error_handler import sanitize_error
from shared.logging_config import get_logger

logger = get_logger("aura.api_gateway.connections")

router = APIRouter(tags=["Connections"])


# ── In-memory store ──────────────────────────────────────────────────

_connections_lock = threading.Lock()
_connections_store: Dict[str, Dict[str, Any]] = {}  # id → connection dict


def _make_connector(conn_type: str, config: ConnectorConfig):
    """Construct a connector via the central registry.

    DB-agnostic: any connector registered with a ``factory`` — built-in
    or a third-party ``aura.connectors`` entry-point plugin — is built
    the same way, with no hardcoded type->class switch to keep in sync.
    """
    return build_connector(conn_type, config)


# ── Models ───────────────────────────────────────────────────────────

class ConnectionCreateRequest(BaseModel):
    name: str
    type: str  # postgresql, mysql, bigquery, sqlite, csv, duckdb
    host: Optional[str] = None
    port: Optional[int] = None
    database: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = None
    ssl: bool = False
    extra: Dict[str, Any] = Field(default_factory=dict)


class ConnectorTableListResponse(BaseModel):
    connector_id: str
    tables: List[str]
    total_count: int


class ProfileTableRequest(BaseModel):
    connector_type: str
    connector_config: Dict[str, Any]
    table_name: str


# ── Connector discovery endpoints ────────────────────────────────────

@router.get("/connectors/available")
async def list_available_connectors(include_unavailable: bool = True):
    """List connectors via the central registry. Set
    ``?include_unavailable=false`` to hide types whose driver isn't installed."""
    specs = available_connectors(include_unavailable=include_unavailable)
    return {"connectors": [s.to_dict() for s in specs]}


@router.get("/connectors/registry")
async def connectors_registry(include_unavailable: bool = True):
    """Full registry payload — same shape as ``/connectors/available`` but
    explicit about being the registry endpoint, for clients that want to
    drive a generic catalog UI off it."""
    specs = available_connectors(include_unavailable=include_unavailable)
    return {
        "success": True,
        "count": len(specs),
        "connectors": [s.to_dict() for s in specs],
    }


@router.post("/connectors/{connector_type}/test")
async def test_connector(connector_type: str, config: Dict[str, Any]):
    """Test connector configuration."""
    try:
        connector_config = ConnectorConfig(
            source_type=SourceType(connector_type),
            name=f"test-{connector_type}",
            **config,
        )
        connector = _make_connector(connector_type, connector_config)
        if connector is None:
            raise ValueError(f"Unknown connector type: {connector_type}")

        connected = await connector.connect()
        if connected:
            tables = await connector.list_tables()
            await connector.disconnect()
            return {"success": True, "message": f"Connected successfully. Found {len(tables)} tables.", "table_count": len(tables)}
        return {"success": False, "message": "Failed to connect", "error": "Connection failed"}
    except Exception as e:
        return {"success": False, "message": "Test failed", "error": sanitize_error(e, logger=logger, context="connector test")}


@router.post("/connectors/{connector_type}/tables")
async def list_connector_tables(connector_type: str, config: Dict[str, Any]) -> ConnectorTableListResponse:
    """List tables from a connector."""
    try:
        connector_config = ConnectorConfig(source_type=SourceType(connector_type), name=f"list-{connector_type}", **config)
        connector = _make_connector(connector_type, connector_config)
        if connector is None:
            raise ValueError(f"Unknown connector type: {connector_type}")
        await connector.connect()
        tables = await connector.list_tables()
        await connector.disconnect()
        return ConnectorTableListResponse(connector_id=f"test-{connector_type}", tables=tables, total_count=len(tables))
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=sanitize_error(e, logger=logger, context=f"list tables {connector_type}"),
        )


@router.post("/connectors/{connector_type}/profile")
async def profile_table(connector_type: str, request: ProfileTableRequest) -> Dict[str, Any]:
    """Profile a table from connector."""
    try:
        connector_config = ConnectorConfig(source_type=SourceType(connector_type), name=f"profile-{connector_type}", **request.connector_config)
        connector = _make_connector(connector_type, connector_config)
        if connector is None:
            raise ValueError(f"Unknown connector type: {connector_type}")
        await connector.connect()
        profile = await connector.profile_table(request.table_name)
        await connector.disconnect()
        return profile
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=sanitize_error(e, logger=logger, context=f"profile table {connector_type}"),
        )


# ── Connection CRUD ──────────────────────────────────────────────────

@router.get("/connections")
async def get_connections(request: Request):
    """List all registered data source connections."""
    from pathlib import Path

    from api_gateway.routers.workspaces import tenant_upload_dir

    with _connections_lock:
        conns = list(_connections_store.values())
    file_sources = 0
    try:
        # Count datasets in the CALLER's per-tenant upload dir (matches
        # GET /files and the dashboard) — consistent + ignores the
        # internal cache dirs beside the workspace folders.
        _tracked = {".csv", ".json", ".parquet", ".xlsx", ".xls"}
        upload_dir = Path(tenant_upload_dir(request))
        if upload_dir.exists():
            file_sources = sum(
                1 for f in upload_dir.iterdir()
                if f.is_file() and f.suffix.lower() in _tracked
            )
    except Exception:
        pass
    return {"success": True, "connections": conns, "count": len(conns), "file_sources": file_sources}


@router.post("/connections")
async def create_connection(req: ConnectionCreateRequest):
    """Register a new data source connection.

    The connector ``type`` is validated against the registry — unknown or
    driver-missing types are rejected at create-time rather than failing
    confusingly later when the user clicks Test."""
    spec = get_connector(req.type)
    if spec is None:
        valid = [s.id for s in available_connectors(include_unavailable=True)]
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": f"Unknown connector type '{req.type}'.",
                "valid_types": valid,
            },
        )
    if not spec.available:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": f"Connector '{req.type}' is registered but unavailable.",
                "reason": spec.unavailable_reason or "driver not installed",
            },
        )

    conn_id = str(_uuid.uuid4())[:12]
    now = datetime.now().isoformat()
    conn = {
        "id": conn_id, "name": req.name, "type": req.type,
        "host": req.host, "port": req.port, "database": req.database,
        "username": req.username, "ssl": req.ssl,
        "is_active": False, "created_at": now, "updated_at": now,
        "last_tested": None, "table_count": 0,
    }
    with _connections_lock:
        _connections_store[conn_id] = conn
    logger.info("Connection created: %s (%s/%s)", conn_id, req.type, req.name)
    return {"success": True, "connection": conn}


@router.post("/connections/{connection_id}/test")
async def test_connection_by_id(connection_id: str):
    """Test an existing connection by ID."""
    with _connections_lock:
        conn = _connections_store.get(connection_id)
    if not conn:
        raise HTTPException(status_code=404, detail="Connection not found")
    try:
        connector_config = ConnectorConfig(
            source_type=SourceType(conn["type"]), name=conn["name"],
            host=conn.get("host", ""), port=conn.get("port", 5432),
            username=conn.get("username", ""), password="",
            database=conn.get("database", ""),
        )
        connector = _make_connector(conn["type"], connector_config)
        if connector is None:
            return {"success": True, "message": f"Connection type '{conn['type']}' registered (test skipped)"}
        connected = await connector.connect()
        if connected:
            tables = await connector.list_tables()
            await connector.disconnect()
            with _connections_lock:
                _connections_store[connection_id]["is_active"] = True
                _connections_store[connection_id]["last_tested"] = datetime.now().isoformat()
                _connections_store[connection_id]["table_count"] = len(tables)
            return {"success": True, "message": f"Connected. Found {len(tables)} tables.", "table_count": len(tables)}
        return {"success": False, "message": "Connection failed"}
    except Exception as e:
        return {"success": False, "message": sanitize_error(e, logger=logger, context="connection test by id")}


@router.delete("/connections/{connection_id}")
async def delete_connection(connection_id: str):
    """Remove a registered connection."""
    with _connections_lock:
        removed = _connections_store.pop(connection_id, None)
    if not removed:
        raise HTTPException(status_code=404, detail="Connection not found")
    return {"success": True, "message": f"Connection '{removed['name']}' deleted"}


@router.get("/connections/{connection_id}/schema")
async def get_connection_schema(connection_id: str):
    """Get table/column schema for a connection."""
    with _connections_lock:
        conn = _connections_store.get(connection_id)
    if not conn:
        raise HTTPException(status_code=404, detail="Connection not found")
    try:
        connector_config = ConnectorConfig(
            source_type=SourceType(conn["type"]), name=conn["name"],
            host=conn.get("host", ""), port=conn.get("port", 5432),
            username=conn.get("username", ""), password="",
            database=conn.get("database", ""),
        )
        connector = _make_connector(conn["type"], connector_config)
        if connector is None:
            return {"success": True, "schema": {}, "message": "Schema introspection not available for this type"}
        await connector.connect()
        tables = await connector.list_tables()
        schema: Dict[str, List[str]] = {}
        for t in tables[:50]:
            try:
                profile = await connector.profile_table(t)
                schema[t] = list(profile.get("columns", {}).keys()) if isinstance(profile.get("columns"), dict) else []
            except Exception:
                schema[t] = []
        await connector.disconnect()
        return {"success": True, "schema": schema, "table_count": len(tables)}
    except Exception as e:
        return {"success": False, "error": sanitize_error(e, logger=logger, context="connection schema"), "schema": {}}


@router.get("/databases/test/{db_type}")
async def test_database_connection(db_type: str):
    """Proxy to database service for connection testing."""
    try:
        import httpx
        db_svc = os.getenv("DATABASE_SERVICE_URL", "http://localhost:8002")
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(f"{db_svc}/databases/test/{db_type}")
            return response.json()
    except Exception as e:
        sanitize_error(e, logger=logger, context=f"database service proxy {db_type}")
        return {"error": "Database service unavailable", "status": "error"}
# ── Connection → Ingest bridge (end-to-end slice) ────────────────────
# Reads rows from an attached connection and streams them through the
# UASR self-healing ingest pipeline in batches, then records a dataset
# profile to the metadata store. This is the seam that proves the whole
# product path — connector → UASR drift/heal → metadata — with real data.

_UASR_URL = os.getenv("AURA_UASR_URL", "http://localhost:8009")

# Conservative identifier guard for table names interpolated into SQL.
_IDENT_RE = __import__("re").compile(r"^[A-Za-z_][A-Za-z0-9_.\"]*$")


class ConnectorIngestRequest(BaseModel):
    """Pull rows from a connector table and stream them through UASR."""
    connector_config: Dict[str, Any] = Field(default_factory=dict)
    table_name: str
    source_id: Optional[str] = None
    batch_size: int = 500
    max_rows: Optional[int] = None
    register_baseline: bool = True


@router.post("/connectors/{connector_type}/ingest")
async def ingest_connector_data(connector_type: str, req: ConnectorIngestRequest):
    """Stream a connector table through the UASR self-healing pipeline.

    Flow: build connector → connect → (optionally) register the first
    batch as the drift baseline → page the table in ``batch_size`` chunks,
    POSTing each chunk to ``/uasr/ingest`` → persist a dataset profile to
    the metadata store → return a per-batch summary (rows, drift, latency).
    """
    import httpx

    if not _IDENT_RE.match(req.table_name):
        raise HTTPException(status_code=400, detail=f"Invalid table name: {req.table_name!r}")
    if req.batch_size < 1 or req.batch_size > 50000:
        raise HTTPException(status_code=400, detail="batch_size must be in [1, 50000]")

    try:
        connector_config = ConnectorConfig(
            source_type=SourceType(connector_type),
            name=f"ingest-{connector_type}",
            **req.connector_config,
        )
    except (ValueError, TypeError) as e:
        raise HTTPException(status_code=400, detail=sanitize_error(e, logger=logger, context="ingest config"))

    connector = _make_connector(connector_type, connector_config)
    if connector is None:
        raise HTTPException(status_code=400, detail=f"Connector type '{connector_type}' does not support ingest")

    connected = await connector.connect()
    if not connected:
        raise HTTPException(status_code=502, detail=f"Could not connect to {connector_type} source")

    source_id = req.source_id or f"{connector_type}:{req.table_name}"
    batches: List[Dict[str, Any]] = []
    total_rows = 0
    drift_events = 0

    try:
        schema = await connector.get_table_schema(req.table_name)
        schema_snapshot = {
            c["name"]: c.get("type") for c in schema.get("columns", [])
        } if isinstance(schema, dict) else None

        offset = 0
        first = True
        async with httpx.AsyncClient(timeout=120) as client:
            while True:
                remaining = None if req.max_rows is None else max(0, req.max_rows - total_rows)
                if remaining == 0:
                    break
                limit = req.batch_size if remaining is None else min(req.batch_size, remaining)
                query = f'SELECT * FROM {req.table_name} LIMIT {limit} OFFSET {offset}'
                rows = await connector.execute_query(query, limit=limit)
                if not rows:
                    break

                columns = list(rows[0].keys())

                if first and req.register_baseline:
                    try:
                        await client.post(
                            f"{_UASR_URL}/uasr/baseline",
                            json={
                                "source_id": source_id,
                                "columns": columns,
                                "rows": rows,
                                "schema_snapshot": schema_snapshot,
                            },
                        )
                    except httpx.HTTPError as e:
                        logger.warning("UASR baseline registration failed: %s", e)
                    first = False

                try:
                    resp = await client.post(
                        f"{_UASR_URL}/uasr/ingest",
                        json={
                            "source_id": source_id,
                            "columns": columns,
                            "rows": rows,
                            "schema_snapshot": schema_snapshot,
                            "metadata": {"connector_type": connector_type, "table": req.table_name},
                        },
                    )
                    result = resp.json()
                except httpx.HTTPError as e:
                    await connector.disconnect()
                    raise HTTPException(status_code=502, detail=f"UASR ingest failed: {sanitize_error(e, logger=logger, context='uasr ingest')}")

                drifted = bool(result.get("drift_detected") or (result.get("drift") or {}).get("detected"))
                if drifted:
                    drift_events += 1
                total_rows += len(rows)
                batches.append({
                    "offset": offset,
                    "rows": len(rows),
                    "drift_detected": drifted,
                    "result": result,
                })

                offset += len(rows)
                if len(rows) < limit:
                    break
    finally:
        await connector.disconnect()

    # Persist a dataset profile to the metadata store (best-effort).
    profile_recorded = False
    try:
        from metadata_store.repository import get_repository
        async for repo in get_repository():
            await repo.upsert_dataset_profile(
                file_id=source_id,
                dataset_name=f"{connector_type}:{req.table_name}",
                profile={
                    "source": connector_type,
                    "table": req.table_name,
                    "schema": schema_snapshot,
                    "batches": len(batches),
                    "drift_events": drift_events,
                },
                rows_count=total_rows,
                columns_count=len(schema_snapshot) if schema_snapshot else None,
            )
            profile_recorded = True
            break
    except ImportError:
        logger.info("metadata_store repository unavailable; skipping profile record")
    except Exception as e:
        logger.warning("dataset profile record failed: %s", sanitize_error(e, logger=logger, context="ingest profile"))

    return {
        "success": True,
        "source_id": source_id,
        "table": req.table_name,
        "total_rows": total_rows,
        "batches": len(batches),
        "drift_events": drift_events,
        "profile_recorded": profile_recorded,
        "detail": batches,
    }
