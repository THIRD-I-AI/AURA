"""
AURA Universal Database Adapter
================================
Provider-agnostic interface for all database backends.
Supports: Regular SQL, Vector embeddings (image/AI), Spatial/4D (VR/GIS).

Adapters
--------
- PostgresAdapter   : Full-featured (SQL + pgvector + PostGIS)
- DuckDBAdapter     : Fast local analytics (SQL + Parquet/CSV direct query)

Usage
-----
    from shared.database_adapter import get_adapter

    db = get_adapter()                       # auto-detect from env
    db = get_adapter("postgresql", host=...) # explicit
    await db.connect()
    rows = await db.execute_query("SELECT * FROM users LIMIT 10")
    similar = await db.vector_search("image_assets", embedding, limit=5)
    await db.disconnect()
"""
from __future__ import annotations

import json
import logging
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

logger = logging.getLogger("aura.shared.database_adapter")

# ======================== Types ========================

class BackendType(str, Enum):
    POSTGRESQL = "postgresql"
    DUCKDB     = "duckdb"
    MYSQL      = "mysql"
    BIGQUERY   = "bigquery"
    SNOWFLAKE  = "snowflake"


@dataclass
class AdapterConfig:
    """Connection configuration for any backend."""
    backend: BackendType = BackendType.POSTGRESQL
    host: str = "localhost"
    port: int = 5432
    database: str = "aura_vault"
    username: str = "postgres"
    password: str = ""
    # DuckDB-specific
    db_path: str = ""                     # path to .duckdb file or ":memory:"
    # Extra kwargs forwarded to driver
    extra: Dict[str, Any] = field(default_factory=dict)


# ======================== Abstract Base ========================

class DatabaseAdapter(ABC):
    """
    Universal interface that every AURA backend must implement.
    Covers three data planes:
      1. Relational (SQL)
      2. Vector    (embeddings / similarity search)
      3. Spatial   (PostGIS / 4D VR)
    """

    def __init__(self, config: AdapterConfig) -> None:
        self.config = config
        self._connected = False

    @property
    def is_connected(self) -> bool:
        return self._connected

    # ---------- lifecycle ----------
    @abstractmethod
    async def connect(self) -> bool: ...

    @abstractmethod
    async def disconnect(self) -> bool: ...

    @abstractmethod
    async def health_check(self) -> Dict[str, Any]: ...

    # ---------- relational ----------
    @abstractmethod
    async def execute_query(
        self, query: str, params: Optional[Sequence[Any]] = None, limit: int = 1000,
    ) -> List[Dict[str, Any]]: ...

    @abstractmethod
    async def execute_write(
        self, query: str, params: Optional[Sequence[Any]] = None,
    ) -> int:
        """Execute INSERT/UPDATE/DELETE.  Returns affected-row count."""
        ...

    @abstractmethod
    async def list_tables(self) -> List[str]: ...

    @abstractmethod
    async def get_table_schema(self, table: str) -> Dict[str, Any]: ...

    # ---------- vector ----------
    async def vector_search(
        self,
        table: str,
        embedding: List[float],
        *,
        column: str = "embedding",
        limit: int = 10,
        metric: str = "cosine",
    ) -> List[Dict[str, Any]]:
        """Find nearest neighbours by vector similarity.

        Default impl raises NotImplementedError — override in adapters
        that support pgvector or similar.
        """
        raise NotImplementedError(f"{self.__class__.__name__} does not support vector search")

    async def store_vector(
        self,
        table: str,
        data: Dict[str, Any],
        embedding: List[float],
        *,
        column: str = "embedding",
    ) -> Optional[str]:
        """Insert a row with an embedding vector.  Returns the row id."""
        raise NotImplementedError(f"{self.__class__.__name__} does not support vector storage")

    # ---------- spatial ----------
    async def spatial_query(
        self,
        query: str,
        params: Optional[Sequence[Any]] = None,
    ) -> List[Dict[str, Any]]:
        """Run a PostGIS / spatial query.  Default delegates to execute_query."""
        return await self.execute_query(query, params)

    async def store_point(
        self,
        table: str,
        data: Dict[str, Any],
        x: float, y: float, z: float = 0.0,
        *,
        geom_column: str = "location",
        srid: int = 4326,
    ) -> Optional[str]:
        """Insert a row with a POINTZ geometry.  Returns the row id."""
        raise NotImplementedError(f"{self.__class__.__name__} does not support spatial storage")

    # ---------- diagnostics ----------
    async def capabilities(self) -> Dict[str, bool]:
        """Report which data-planes this adapter supports."""
        return {
            "relational": True,
            "vector": False,
            "spatial": False,
        }


# ======================== PostgreSQL Adapter ========================

class PostgresAdapter(DatabaseAdapter):
    """
    Full-featured adapter: SQL + pgvector + PostGIS.
    Uses asyncpg for async I/O.
    """

    def __init__(self, config: AdapterConfig) -> None:
        super().__init__(config)
        self._pool: Any = None
        self._has_pgvector = False
        self._has_postgis = False

    async def connect(self) -> bool:
        try:
            import asyncpg  # type: ignore[import-untyped]
        except ImportError:
            logger.warning("PostgresAdapter: asyncpg is not installed")
            return False
        try:
            self._pool = await asyncpg.create_pool(
                host=self.config.host,
                port=self.config.port,
                database=self.config.database,
                user=self.config.username,
                password=self.config.password,
                min_size=1,
                max_size=int(os.getenv("DB_POOL_SIZE", "10")),
                **self.config.extra,
            )
            self._connected = True
            # Probe extensions
            await self._probe_extensions()
            return True
        except Exception as exc:
            logger.warning("PostgresAdapter connect failed: %s", exc)
            return False

    async def disconnect(self) -> bool:
        if self._pool:
            await self._pool.close()
        self._connected = False
        return True

    async def health_check(self) -> Dict[str, Any]:
        if not self._pool:
            return {"ok": False, "error": "not connected"}
        try:
            async with self._pool.acquire() as conn:
                val = await conn.fetchval("SELECT 1")
            return {
                "ok": True,
                "backend": "postgresql",
                "pgvector": self._has_pgvector,
                "postgis": self._has_postgis,
            }
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    # ---------- relational ----------

    async def execute_query(
        self, query: str, params: Optional[Sequence[Any]] = None, limit: int = 1000,
    ) -> List[Dict[str, Any]]:
        if not self._pool:
            return []
        async with self._pool.acquire() as conn:
            if params:
                rows = await conn.fetch(query, *params)
            else:
                rows = await conn.fetch(query)
            return [dict(r) for r in rows[:limit]]

    async def execute_write(
        self, query: str, params: Optional[Sequence[Any]] = None,
    ) -> int:
        if not self._pool:
            return 0
        async with self._pool.acquire() as conn:
            if params:
                result = await conn.execute(query, *params)
            else:
                result = await conn.execute(query)
            # asyncpg returns e.g. "INSERT 0 1"
            try:
                return int(result.split()[-1])
            except (ValueError, IndexError):
                return 0

    async def list_tables(self) -> List[str]:
        rows = await self.execute_query(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'public' ORDER BY table_name"
        )
        return [r["table_name"] for r in rows]

    async def get_table_schema(self, table: str) -> Dict[str, Any]:
        rows = await self.execute_query(
            "SELECT column_name, data_type, is_nullable "
            "FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = $1 "
            "ORDER BY ordinal_position",
            [table],
        )
        return {
            "table": table,
            "columns": [
                {"name": r["column_name"], "type": r["data_type"],
                 "nullable": r["is_nullable"] == "YES"}
                for r in rows
            ],
        }

    # ---------- vector (pgvector) ----------

    async def vector_search(
        self,
        table: str,
        embedding: List[float],
        *,
        column: str = "embedding",
        limit: int = 10,
        metric: str = "cosine",
    ) -> List[Dict[str, Any]]:
        if not self._has_pgvector:
            raise NotImplementedError("pgvector extension is not installed")
        op = "<=>" if metric == "cosine" else "<->" if metric == "l2" else "<=>"
        vec_literal = "[" + ",".join(str(v) for v in embedding) + "]"
        query = (
            f"SELECT *, 1 - ({column} {op} $1::vector) AS similarity "
            f"FROM {table} WHERE {column} IS NOT NULL "
            f"ORDER BY {column} {op} $1::vector LIMIT {limit}"
        )
        return await self.execute_query(query, [vec_literal])

    async def store_vector(
        self,
        table: str,
        data: Dict[str, Any],
        embedding: List[float],
        *,
        column: str = "embedding",
    ) -> Optional[str]:
        if not self._has_pgvector:
            raise NotImplementedError("pgvector extension is not installed")
        cols = list(data.keys()) + [column]
        placeholders = [f"${i+1}" for i in range(len(data))]
        placeholders.append(f"${len(data)+1}::vector")
        vec_literal = "[" + ",".join(str(v) for v in embedding) + "]"
        values = list(data.values()) + [vec_literal]
        query = (
            f"INSERT INTO {table} ({', '.join(cols)}) "
            f"VALUES ({', '.join(placeholders)}) RETURNING *"
        )
        rows = await self.execute_query(query, values)
        if rows:
            # return the first UUID or id column
            first = rows[0]
            for key in ("image_id", "vector_id", "memory_id", "id"):
                if key in first:
                    return str(first[key])
        return None

    # ---------- spatial (PostGIS) ----------

    async def store_point(
        self,
        table: str,
        data: Dict[str, Any],
        x: float, y: float, z: float = 0.0,
        *,
        geom_column: str = "location",
        srid: int = 4326,
    ) -> Optional[str]:
        if not self._has_postgis:
            raise NotImplementedError("PostGIS extension is not installed")
        cols = list(data.keys()) + [geom_column]
        idx = len(data)
        placeholders = [f"${i+1}" for i in range(len(data))]
        placeholders.append(f"ST_SetSRID(ST_MakePoint(${idx+1}, ${idx+2}, ${idx+3}), {srid})")
        values = list(data.values()) + [x, y, z]
        query = (
            f"INSERT INTO {table} ({', '.join(cols)}) "
            f"VALUES ({', '.join(placeholders)}) RETURNING *"
        )
        rows = await self.execute_query(query, values)
        if rows:
            first = rows[0]
            for key in ("telemetry_id", "object_id", "id"):
                if key in first:
                    return str(first[key])
        return None

    # ---------- diagnostics ----------

    async def capabilities(self) -> Dict[str, bool]:
        return {
            "relational": True,
            "vector": self._has_pgvector,
            "spatial": self._has_postgis,
        }

    async def _probe_extensions(self) -> None:
        """Check which extensions are installed."""
        try:
            rows = await self.execute_query(
                "SELECT extname FROM pg_extension WHERE extname IN ('vector', 'postgis')"
            )
            names = {r["extname"] for r in rows}
            self._has_pgvector = "vector" in names
            self._has_postgis = "postgis" in names
        except Exception:
            pass


# ======================== DuckDB Adapter ========================

class DuckDBAdapter(DatabaseAdapter):
    """
    Fast local analytics.  Queries CSV / Parquet / JSON directly.
    In-process — no server needed.
    """

    def __init__(self, config: AdapterConfig) -> None:
        super().__init__(config)
        self._conn: Any = None

    async def connect(self) -> bool:
        try:
            import duckdb  # type: ignore[import-untyped]
        except ImportError:
            logger.warning("DuckDBAdapter: duckdb is not installed (pip install duckdb)")
            return False
        try:
            path = self.config.db_path or ":memory:"
            self._conn = duckdb.connect(path)
            self._connected = True
            return True
        except Exception as exc:
            logger.warning("DuckDBAdapter connect failed: %s", exc)
            return False

    async def disconnect(self) -> bool:
        if self._conn:
            self._conn.close()
            self._conn = None
        self._connected = False
        return True

    async def health_check(self) -> Dict[str, Any]:
        if not self._conn:
            return {"ok": False, "error": "not connected"}
        try:
            self._conn.execute("SELECT 1")
            return {"ok": True, "backend": "duckdb"}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    async def execute_query(
        self, query: str, params: Optional[Sequence[Any]] = None, limit: int = 1000,
    ) -> List[Dict[str, Any]]:
        if not self._conn:
            return []
        try:
            if params:
                result = self._conn.execute(query, list(params))
            else:
                result = self._conn.execute(query)
            cols = [desc[0] for desc in result.description]
            return [dict(zip(cols, row)) for row in result.fetchmany(limit)]
        except Exception as exc:
            logger.warning("DuckDB query failed: %s", exc)
            return []

    async def execute_write(
        self, query: str, params: Optional[Sequence[Any]] = None,
    ) -> int:
        if not self._conn:
            return 0
        try:
            if params:
                self._conn.execute(query, list(params))
            else:
                self._conn.execute(query)
            return self._conn.execute("SELECT changes()").fetchone()[0]  # type: ignore
        except Exception as exc:
            logger.warning("DuckDB write failed: %s", exc)
            return 0

    async def list_tables(self) -> List[str]:
        rows = await self.execute_query("SHOW TABLES")
        # DuckDB returns 'name' column
        return [r.get("name", r.get("Name", "")) for r in rows]

    async def get_table_schema(self, table: str) -> Dict[str, Any]:
        rows = await self.execute_query(f"DESCRIBE {table}")
        return {
            "table": table,
            "columns": [
                {"name": r.get("column_name", r.get("Field", "")),
                 "type": r.get("column_type", r.get("Type", "")),
                 "nullable": r.get("null", "YES") == "YES"}
                for r in rows
            ],
        }

    async def capabilities(self) -> Dict[str, bool]:
        return {"relational": True, "vector": False, "spatial": False}


# ======================== Factory ========================

_adapter_cache: Dict[str, DatabaseAdapter] = {}


def _config_from_env() -> AdapterConfig:
    """Build an AdapterConfig from AURA_VAULT_* env vars."""
    backend = os.getenv("AURA_VAULT_BACKEND", "postgresql").lower()
    return AdapterConfig(
        backend=BackendType(backend),
        host=os.getenv("AURA_VAULT_HOST", "localhost"),
        port=int(os.getenv("AURA_VAULT_PORT", "5432")),
        database=os.getenv("AURA_VAULT_DATABASE", "aura_vault"),
        username=os.getenv("AURA_VAULT_USER", "postgres"),
        password=os.getenv("AURA_VAULT_PASSWORD", ""),
        db_path=os.getenv("AURA_VAULT_DUCKDB_PATH", ":memory:"),
    )


def get_adapter(
    backend: Optional[str] = None,
    *,
    config: Optional[AdapterConfig] = None,
    cache_key: str = "default",
    **overrides: Any,
) -> DatabaseAdapter:
    """
    Factory that returns a DatabaseAdapter instance.

    Priority:
      1. Explicit ``config`` object
      2. Explicit ``backend`` string + ``overrides``
      3. AURA_VAULT_* environment variables

    Instances are cached by ``cache_key`` so the same pool is reused.
    """
    if cache_key in _adapter_cache:
        return _adapter_cache[cache_key]

    if config is None:
        if backend:
            cfg = _config_from_env()
            cfg.backend = BackendType(backend.lower())
            for k, v in overrides.items():
                if hasattr(cfg, k):
                    setattr(cfg, k, v)
            config = cfg
        else:
            config = _config_from_env()

    adapter: DatabaseAdapter
    if config.backend == BackendType.DUCKDB:
        adapter = DuckDBAdapter(config)
    else:
        adapter = PostgresAdapter(config)

    _adapter_cache[cache_key] = adapter
    return adapter


def available_adapters() -> List[Dict[str, Any]]:
    """Return which adapters are importable."""
    result: List[Dict[str, Any]] = []
    for name, pkg in [("postgresql", "asyncpg"), ("duckdb", "duckdb")]:
        try:
            __import__(pkg)
            result.append({"backend": name, "available": True})
        except ImportError:
            result.append({"backend": name, "available": False})
    return result
