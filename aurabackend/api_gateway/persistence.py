"""
API Gateway persistence layer — Sprint P-1.

Replaces three in-memory dictionaries in ``routers/queries.py`` with a
real Postgres-backed (SQLite-in-dev) SQLAlchemy persistence layer:

  * ``_query_history_store``  → ``gateway_query_history`` table
  * ``_saved_queries_store``  → ``gateway_saved_queries`` table
  * ``_share_tokens_store``   → ``gateway_share_tokens`` table

Why this matters
----------------
Pre-Sprint-P-1, the gateway could not run with ``replicas > 1``: each
pod had its own isolated history + saved-queries + share tokens. With
this module, all three stores live in a shared database — horizontal
scaling becomes safe. Also fixes the O(n) share-token revoke and the
linear scans through saved-queries lists.

Schema design
-------------
Per the inventory from the survey phase:

* **query_history**: append-only, capped at 200 globally (no workspace
  scoping). Newest-first reads via an index on ``timestamp``.
* **saved_queries**: per-workspace, capped at 500 per workspace.
  Composite index on ``(workspace_id, created_ts DESC)`` for the
  ordered list endpoint. ``starred`` toggle drives a secondary sort.
* **share_tokens**: opaque URL-safe token → saved_query_id mapping.
  Indexed on ``saved_query_id`` so the bulk-revoke-on-delete path is
  O(log n) instead of the O(n) dict scan it was before.

This module is self-contained: it owns its engine, models, and
session factory. It does NOT depend on ``metadata_store/`` (which is
a separate microservice running in a different process). Sharing the
DB URL across services is a deployment concern, not a code one.
"""
from __future__ import annotations

import json
import logging
import os
import secrets
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Dict, List, Optional

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    delete,
    func,
    select,
    update,
)
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

logger = logging.getLogger("aura.api_gateway.persistence")


# ── ORM Base + models ─────────────────────────────────────────────────


class Base(DeclarativeBase):
    """Single DeclarativeBase shared by all gateway tables.

    Kept separate from metadata_store's Base so the two services'
    schemas evolve independently and a Postgres deployment can host
    them in distinct databases or schemas if operators want isolation.
    """


class QueryHistoryRow(Base):
    """One row per executed query — append-only, capped at 200.

    ``timestamp`` is the wire-format ISO string (matches the historic
    dict shape so endpoint responses are byte-identical to the
    in-memory version). ``created_ts`` is a numeric column we index
    on for cheap newest-first ORDER BY.
    """

    __tablename__ = "gateway_query_history"

    id = Column(String(64), primary_key=True)
    prompt = Column(Text, nullable=False, default="")
    sql = Column(Text, nullable=False, default="")
    status = Column(String(32), nullable=False, default="success")
    rows = Column(Integer, nullable=False, default=0)
    execution_time = Column(Float, nullable=False, default=0.0)
    timestamp = Column(String(64), nullable=False)
    created_ts = Column(Float, nullable=False, index=True)

    __table_args__ = (
        Index("ix_query_history_created_ts_desc", created_ts.desc()),
    )


class SavedQueryRow(Base):
    """One row per saved query, workspace-scoped, capped at 500/ws.

    The ``schedule`` field is canonical-JSON text — Pydantic on the
    way out parses back into a dict. Storing as TEXT (not JSONB)
    keeps the schema portable across SQLite + Postgres without
    dialect-specific column types; the schedule field is small so
    the indexing benefit of JSONB doesn't matter here.
    """

    __tablename__ = "gateway_saved_queries"

    id = Column(String(64), primary_key=True)
    workspace_id = Column(String(64), nullable=False, index=True)
    name = Column(String(255), nullable=False)
    sql = Column(Text, nullable=False)
    prompt = Column(Text, nullable=True)
    starred = Column(Boolean, nullable=False, default=False)
    created_at = Column(String(64), nullable=False)
    created_ts = Column(Float, nullable=False)
    updated_at = Column(String(64), nullable=False)
    schedule_json = Column(Text, nullable=True)
    next_run_at = Column(String(64), nullable=True)
    last_run_at = Column(String(64), nullable=True)

    __table_args__ = (
        # Composite index drives the ORDER BY starred DESC, created_ts DESC
        # used by the list endpoint. Postgres + SQLite both honour
        # multi-column indexes the same way.
        Index(
            "ix_saved_queries_ws_starred_created",
            workspace_id, starred.desc(), created_ts.desc(),
        ),
    )


class ShareTokenRow(Base):
    """One row per active share token.

    The token is the primary key — it's already URL-safe + unique
    (24 bytes from ``secrets.token_urlsafe``). The saved_query_id
    has a separate index so the cascade-revoke path on
    ``DELETE /saved-queries/{id}`` hits an index instead of doing
    a sequential scan.
    """

    __tablename__ = "gateway_share_tokens"

    token = Column(String(64), primary_key=True)
    saved_query_id = Column(
        String(64),
        ForeignKey("gateway_saved_queries.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )


# ── Engine + session factory ─────────────────────────────────────────


_engine: Optional[AsyncEngine] = None
_session_factory: Optional[async_sessionmaker[AsyncSession]] = None
# Tracks whether the schema has been created in the current engine.
# Reset when the engine itself is recreated (e.g. in tests via
# ``reset_all_for_tests`` or a fixture that swaps GATEWAY_DATABASE_URL).
_schema_initialized: bool = False


def database_url() -> str:
    """Resolve the gateway's database URL.

    Default: ``sqlite+aiosqlite:///data/gateway.db`` (dev). Production
    deployments override via ``GATEWAY_DATABASE_URL`` to point at a
    shared Postgres instance — that's how horizontal scaling works
    after this sprint.
    """
    return os.getenv(
        "GATEWAY_DATABASE_URL",
        "sqlite+aiosqlite:///data/gateway.db",
    )


def get_engine() -> AsyncEngine:
    """Lazy-initialised async engine. One per process."""
    global _engine
    if _engine is None:
        url = database_url()
        # SQLite needs check_same_thread=False for async use. Postgres
        # doesn't care about the kwarg.
        connect_args: Dict[str, Any] = {}
        if url.startswith("sqlite"):
            connect_args["check_same_thread"] = False
        _engine = create_async_engine(
            url, echo=False, future=True, connect_args=connect_args,
        )
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            get_engine(), expire_on_commit=False,
        )
    return _session_factory


async def init_database() -> None:
    """Create all tables on first run. Idempotent.

    Called from the gateway's FastAPI lifespan and lazily from
    ``session_scope`` on first use so test contexts that bypass the
    lifespan (e.g. unit tests that import a router directly) still
    get a working DB. Production deployments should run Alembic
    migrations instead — ``create_all`` is the dev / test fallback."""
    global _schema_initialized
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    _schema_initialized = True
    logger.info("api_gateway persistence ready (url=%s)", database_url())


async def close_database() -> None:
    """Dispose the engine on shutdown."""
    global _engine, _session_factory, _schema_initialized
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _session_factory = None
        _schema_initialized = False


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """Yield a session inside a transaction. Commits on success,
    rolls back on exception.

    Lazily initialises the schema on first call — fixes the test
    failure mode where unit tests import a router but don't run the
    lifespan, so the persistence tables never get created. Idempotent
    via the ``_schema_initialized`` flag."""
    global _schema_initialized
    if not _schema_initialized:
        await init_database()
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


# ── Repository — async CRUD methods ──────────────────────────────────


# Per the audit and the original module: query_history cap = 200,
# saved_queries cap = 500 PER WORKSPACE. Centralise the magic numbers
# here so the repository owns the policy.
QUERY_HISTORY_CAP = 200
SAVED_QUERIES_PER_WORKSPACE_CAP = 500


def _row_to_history_dict(row: QueryHistoryRow) -> Dict[str, Any]:
    """Translate ORM row → wire dict (matches historic in-memory shape)."""
    return {
        "id": row.id,
        "prompt": row.prompt,
        "sql": row.sql,
        "status": row.status,
        "rows": row.rows,
        "executionTime": row.execution_time,
        "timestamp": row.timestamp,
    }


def _row_to_saved_query_dict(row: SavedQueryRow) -> Dict[str, Any]:
    """Translate ORM row → wire dict. ``schedule_json`` decodes back
    into the schedule dict the API contract exposes."""
    out: Dict[str, Any] = {
        "id": row.id,
        "workspace_id": row.workspace_id,
        "name": row.name,
        "sql": row.sql,
        "prompt": row.prompt,
        "starred": bool(row.starred),
        "created_at": row.created_at,
        "created_ts": row.created_ts,
        "updated_at": row.updated_at,
    }
    if row.schedule_json:
        try:
            out["schedule"] = json.loads(row.schedule_json)
        except json.JSONDecodeError:
            logger.warning(
                "saved_query %s has malformed schedule_json; ignoring", row.id,
            )
    if row.next_run_at is not None:
        out["next_run_at"] = row.next_run_at
    if row.last_run_at is not None:
        out["last_run_at"] = row.last_run_at
    return out


# ── Query history ──


async def insert_query_history(record: Dict[str, Any]) -> None:
    """Persist one query-history record + evict the oldest beyond the
    200-row cap. Atomic inside one transaction."""
    async with session_scope() as s:
        row = QueryHistoryRow(
            id=record["id"],
            prompt=record.get("prompt", "") or "",
            sql=record.get("sql", "") or "",
            status=record.get("status", "success"),
            rows=int(record.get("rows", 0) or 0),
            execution_time=float(record.get("executionTime", 0.0) or 0.0),
            timestamp=record.get(
                "timestamp",
                datetime.now(timezone.utc).isoformat(),
            ),
            created_ts=time.time(),
        )
        s.add(row)
        await s.flush()
        # Cap eviction: delete rows beyond the 200th newest. Postgres
        # + SQLite both support the subquery pattern.
        keep_ids_q = (
            select(QueryHistoryRow.id)
            .order_by(QueryHistoryRow.created_ts.desc())
            .limit(QUERY_HISTORY_CAP)
        )
        keep_ids = (await s.execute(keep_ids_q)).scalars().all()
        if keep_ids:
            await s.execute(
                delete(QueryHistoryRow).where(
                    QueryHistoryRow.id.notin_(keep_ids),
                ),
            )


async def list_query_history(
    *,
    limit: int = 50,
    status_filter: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Newest-first slice of the history. ``status_filter`` is an
    exact-match constraint (or 'all' / None to skip filtering)."""
    async with session_scope() as s:
        stmt = select(QueryHistoryRow).order_by(
            QueryHistoryRow.created_ts.desc(),
        )
        if status_filter and status_filter != "all":
            stmt = stmt.where(QueryHistoryRow.status == status_filter)
        stmt = stmt.limit(limit)
        rows = (await s.execute(stmt)).scalars().all()
        return [_row_to_history_dict(r) for r in rows]


# ── Saved queries ──


async def insert_saved_query(record: Dict[str, Any]) -> Dict[str, Any]:
    """Insert + enforce the 500-per-workspace cap.

    Returns the wire-shape dict (so endpoints can return it without a
    second roundtrip)."""
    workspace_id = record.get("workspace_id") or "default"
    async with session_scope() as s:
        row = SavedQueryRow(
            id=record["id"],
            workspace_id=workspace_id,
            name=record.get("name", "") or "",
            sql=record.get("sql", "") or "",
            prompt=record.get("prompt"),
            starred=bool(record.get("starred", False)),
            created_at=record["created_at"],
            created_ts=float(record["created_ts"]),
            updated_at=record["updated_at"],
            schedule_json=(
                json.dumps(record["schedule"], sort_keys=True, separators=(",", ":"))
                if record.get("schedule") is not None
                else None
            ),
            next_run_at=record.get("next_run_at"),
            last_run_at=record.get("last_run_at"),
        )
        s.add(row)
        await s.flush()
        # Cap eviction PER WORKSPACE.
        keep_ids_q = (
            select(SavedQueryRow.id)
            .where(SavedQueryRow.workspace_id == workspace_id)
            .order_by(SavedQueryRow.created_ts.desc())
            .limit(SAVED_QUERIES_PER_WORKSPACE_CAP)
        )
        keep_ids = (await s.execute(keep_ids_q)).scalars().all()
        if keep_ids:
            await s.execute(
                delete(SavedQueryRow)
                .where(SavedQueryRow.workspace_id == workspace_id)
                .where(SavedQueryRow.id.notin_(keep_ids)),
            )
        return _row_to_saved_query_dict(row)


async def list_saved_queries(workspace_id: str) -> List[Dict[str, Any]]:
    """Workspace-filtered list, starred-first then newest-first.
    Returns the wire dict shape."""
    async with session_scope() as s:
        stmt = (
            select(SavedQueryRow)
            .where(SavedQueryRow.workspace_id == workspace_id)
            .order_by(
                SavedQueryRow.starred.desc(),
                SavedQueryRow.created_ts.desc(),
            )
        )
        rows = (await s.execute(stmt)).scalars().all()
        return [_row_to_saved_query_dict(r) for r in rows]


async def get_saved_query(
    query_id: str, workspace_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Fetch one saved query by id. When ``workspace_id`` is set the
    fetch is workspace-filtered (private endpoints). When None the
    fetch is unscoped (the public share-token endpoint)."""
    async with session_scope() as s:
        stmt = select(SavedQueryRow).where(SavedQueryRow.id == query_id)
        if workspace_id is not None:
            stmt = stmt.where(SavedQueryRow.workspace_id == workspace_id)
        row = (await s.execute(stmt)).scalar_one_or_none()
        return _row_to_saved_query_dict(row) if row is not None else None


async def update_saved_query(
    query_id: str, workspace_id: str, fields: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """Apply a partial update + bump updated_at. Returns the post-
    update wire dict (or None if the row doesn't exist / wrong ws)."""
    async with session_scope() as s:
        stmt = (
            select(SavedQueryRow)
            .where(SavedQueryRow.id == query_id)
            .where(SavedQueryRow.workspace_id == workspace_id)
        )
        row = (await s.execute(stmt)).scalar_one_or_none()
        if row is None:
            return None
        for key in ("name", "starred", "schedule", "next_run_at", "last_run_at"):
            if key in fields:
                if key == "schedule":
                    v = fields[key]
                    row.schedule_json = (
                        json.dumps(v, sort_keys=True, separators=(",", ":"))
                        if v is not None else None
                    )
                else:
                    setattr(row, key, fields[key])
        row.updated_at = fields.get(
            "updated_at",
            datetime.now(timezone.utc).isoformat(),
        )
        await s.flush()
        return _row_to_saved_query_dict(row)


async def delete_saved_query(query_id: str, workspace_id: str) -> bool:
    """Delete + cascade-revoke share tokens. Returns False if the row
    didn't exist in the requested workspace."""
    async with session_scope() as s:
        # Confirm the row exists in the workspace before delete.
        stmt = (
            select(SavedQueryRow.id)
            .where(SavedQueryRow.id == query_id)
            .where(SavedQueryRow.workspace_id == workspace_id)
        )
        existing = (await s.execute(stmt)).scalar_one_or_none()
        if existing is None:
            return False
        # Cascade-revoke any share tokens. The FK has ondelete=CASCADE
        # so SQLite/Postgres will do this automatically, but we run
        # an explicit DELETE for the SQLite dialect which sometimes
        # ignores FK constraints unless explicitly enabled.
        await s.execute(
            delete(ShareTokenRow).where(
                ShareTokenRow.saved_query_id == query_id,
            ),
        )
        await s.execute(
            delete(SavedQueryRow).where(SavedQueryRow.id == query_id),
        )
        return True


# ── Share tokens ──


async def get_or_create_share_token(query_id: str) -> str:
    """Idempotent: returns the existing token for this query_id or
    mints a new one. The endpoint-level workspace check happens before
    this call — by the time we're here we already trust the caller."""
    async with session_scope() as s:
        existing = (
            await s.execute(
                select(ShareTokenRow.token).where(
                    ShareTokenRow.saved_query_id == query_id,
                ),
            )
        ).scalar_one_or_none()
        if existing is not None:
            return existing
        token = secrets.token_urlsafe(24)
        s.add(ShareTokenRow(token=token, saved_query_id=query_id))
        return token


async def lookup_share_token(token: str) -> Optional[str]:
    """Return the saved_query_id this token points at, or None."""
    async with session_scope() as s:
        return (
            await s.execute(
                select(ShareTokenRow.saved_query_id).where(
                    ShareTokenRow.token == token,
                ),
            )
        ).scalar_one_or_none()


async def revoke_share_tokens_for_query(query_id: str) -> int:
    """Delete every token pointing at a query — O(log n) via the
    saved_query_id index. Returns the row-count deleted (useful for
    audit telemetry)."""
    async with session_scope() as s:
        result = await s.execute(
            delete(ShareTokenRow).where(
                ShareTokenRow.saved_query_id == query_id,
            ),
        )
        return result.rowcount or 0


async def revoke_one_share_token(token: str) -> None:
    """Delete a specific token (used when a public-share lookup hits a
    dangling token whose target query was deleted)."""
    async with session_scope() as s:
        await s.execute(
            delete(ShareTokenRow).where(ShareTokenRow.token == token),
        )


# ── Test-only helper ──


async def reset_all_for_tests() -> None:
    """Drop + recreate every gateway table. Used by tests that need a
    fresh slate. NEVER call this from production code — it nukes user
    data."""
    global _schema_initialized
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    _schema_initialized = True


__all__ = [
    "Base",
    "QueryHistoryRow",
    "SavedQueryRow",
    "ShareTokenRow",
    "QUERY_HISTORY_CAP",
    "SAVED_QUERIES_PER_WORKSPACE_CAP",
    "database_url",
    "get_engine",
    "get_session_factory",
    "init_database",
    "close_database",
    "insert_query_history",
    "list_query_history",
    "insert_saved_query",
    "list_saved_queries",
    "get_saved_query",
    "update_saved_query",
    "delete_saved_query",
    "get_or_create_share_token",
    "lookup_share_token",
    "revoke_share_tokens_for_query",
    "revoke_one_share_token",
    "reset_all_for_tests",
]
