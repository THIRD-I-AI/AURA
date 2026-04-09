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

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from shared.logging_config import get_logger
from connectors import (
    ConnectorConfig,
    SourceType,
    PostgreSQLConnector,
    MySQLConnector,
    BigQueryConnector,
)

logger = get_logger("aura.api_gateway.connections")

router = APIRouter(tags=["Connections"])


# ── In-memory store ──────────────────────────────────────────────────

_connections_lock = threading.Lock()
_connections_store: Dict[str, Dict[str, Any]] = {}  # id → connection dict


def _make_connector(conn_type: str, config: ConnectorConfig):
    """Factory for creating the right connector from type string."""
    if conn_type == "postgresql":
        return PostgreSQLConnector(config)
    elif conn_type == "mysql":
        return MySQLConnector(config)
    elif conn_type == "bigquery":
        return BigQueryConnector(config)
    return None


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
async def list_available_connectors():
    """List available data connectors."""
    return {
        "connectors": [
            {
                "id": "postgresql",
                "name": "PostgreSQL",
                "description": "PostgreSQL database connector",
                "icon": "🐘",
                "config_required": ["host", "port", "username", "password", "database"],
            },
            {
                "id": "mysql",
                "name": "MySQL",
                "description": "MySQL database connector",
                "icon": "🐬",
                "config_required": ["host", "port", "username", "password", "database"],
            },
            {
                "id": "bigquery",
                "name": "Google BigQuery",
                "description": "BigQuery data warehouse connector",
                "icon": "☁️",
                "config_required": ["credentials_json", "database"],
            },
        ]
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
        return {"success": False, "message": "Test failed", "error": str(e)}


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
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))


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
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))


# ── Connection CRUD ──────────────────────────────────────────────────

@router.get("/connections")
async def get_connections():
    """List all registered data source connections."""
    from pathlib import Path

    with _connections_lock:
        conns = list(_connections_store.values())
    file_sources = 0
    try:
        base = Path(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
        upload_dir = base / "data" / "uploads"
        if upload_dir.exists():
            file_sources = len([f for f in upload_dir.iterdir() if f.is_file()])
    except Exception:
        pass
    return {"success": True, "connections": conns, "count": len(conns), "file_sources": file_sources}


@router.post("/connections")
async def create_connection(req: ConnectionCreateRequest):
    """Register a new data source connection."""
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
        return {"success": False, "message": str(e)}


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
        return {"success": False, "error": str(e), "schema": {}}


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
        return {"error": f"Database service unavailable: {str(e)}", "status": "error"}
