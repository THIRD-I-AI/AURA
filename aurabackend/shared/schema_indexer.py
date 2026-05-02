"""
Structured schema indexer.

Walks an uploaded data file via DuckDB introspection, materialises one
``schema_columns`` row per (source, table, column), and upserts into the
metadata DB. Called from the ``/upload`` endpoint as a non-blocking
background task — the upload itself is never delayed by indexing latency
and never fails because indexing fails.

The MCP server's ``metadata_search_columns`` and ``metadata_describe_table``
tools query this table directly. Without this indexer those tools would
have to keep LIKE-grepping ``documents.body`` (the chat router's free-text
schema dump), which is what the architecture review flagged as a
"naive free-text search that promises structured columns".
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from metadata_store.db import get_session_factory
from metadata_store.models import SchemaColumn

logger = logging.getLogger("aura.shared.schema_indexer")

# Sample size for ``sample_values`` per column — small on purpose; the MCP
# tool exposes these to agents so they can reason about value shape, not
# to ship the dataset around.
_SAMPLE_LIMIT = int(os.getenv("AURA_SCHEMA_INDEXER_SAMPLES", "5"))


# ── Public API ────────────────────────────────────────────────────────

async def index_uploaded_file(file_path: str, source_id: Optional[str] = None) -> Dict[str, int]:
    """Introspect ``file_path`` (CSV / Parquet / JSON) via DuckDB and
    upsert one row per column into ``schema_columns``.

    Returns ``{"tables": N, "columns": M}``. Logs and swallows on
    failure — never raises into the upload path.
    """
    sid = source_id or Path(file_path).stem
    try:
        rows = await asyncio.to_thread(_introspect_via_duckdb, file_path)
    except Exception as exc:
        logger.warning("schema_indexer: introspection failed for %s — %s", file_path, exc)
        return {"tables": 0, "columns": 0}

    if not rows:
        return {"tables": 0, "columns": 0}

    try:
        return await _upsert_columns(sid, rows)
    except Exception as exc:
        logger.warning("schema_indexer: persist failed for source=%s — %s", sid, exc)
        return {"tables": 0, "columns": 0}


# ── DuckDB introspection ──────────────────────────────────────────────

def _introspect_via_duckdb(file_path: str) -> List[Dict[str, Any]]:
    """Open the file in a transient in-memory DuckDB, register it as a
    view, and pull column metadata + a small sample. Returns a list of
    dicts shaped for ``_upsert_columns``.

    Runs on a worker thread (sync DuckDB API). Never holds the loop.
    """
    import duckdb

    suffix = Path(file_path).suffix.lower()
    table_name = Path(file_path).stem
    safe_path = file_path.replace("'", "''")  # DuckDB single-quoted path

    if suffix in {".csv", ".tsv", ".txt"}:
        loader = f"read_csv_auto('{safe_path}', sample_size=-1)"
    elif suffix == ".parquet":
        loader = f"read_parquet('{safe_path}')"
    elif suffix in {".json", ".jsonl", ".ndjson"}:
        loader = f"read_json_auto('{safe_path}')"
    else:
        # Excel and other formats aren't first-class in DuckDB; let the
        # caller handle them through their own loader path. Skipping
        # here is preferable to indexing wrong / partial schemas.
        return []

    con = duckdb.connect(":memory:")
    try:
        con.execute(f'CREATE VIEW "{table_name}" AS SELECT * FROM {loader}')

        # Pull column metadata in ordinal order via DESCRIBE — the
        # information_schema view doesn't include views in DuckDB <= 1.0.
        descr = con.execute(f'DESCRIBE "{table_name}"').fetchall()
        # DESCRIBE columns: column_name, column_type, null, key, default, extra
        col_meta = [
            {
                "column_name": row[0],
                "data_type": str(row[1]),
                "is_nullable": str(row[2]).upper() == "YES",
                "ordinal_position": idx + 1,
            }
            for idx, row in enumerate(descr)
        ]

        # Pull sample rows once, then slice per column. Avoids one query
        # per column on big files.
        sample_cur = con.execute(f'SELECT * FROM "{table_name}" LIMIT ?', [_SAMPLE_LIMIT])
        sample_cols = [d[0] for d in sample_cur.description]
        sample_rows = sample_cur.fetchall()
        samples_by_col: Dict[str, List[Any]] = {c: [] for c in sample_cols}
        for r in sample_rows:
            for c, v in zip(sample_cols, r):
                samples_by_col.setdefault(c, []).append(_jsonable(v))

        out: List[Dict[str, Any]] = []
        for meta in col_meta:
            cname = meta["column_name"]
            out.append({
                **meta,
                "table_name": table_name,
                "sample_values": samples_by_col.get(cname, []),
            })
        return out
    finally:
        try:
            con.close()
        except Exception:
            pass


def _jsonable(v: Any) -> Any:
    """Coerce DuckDB return values to JSON-safe types so ``sample_values``
    can be persisted as JSON without a custom encoder."""
    if v is None or isinstance(v, (str, int, float, bool)):
        return v
    if hasattr(v, "isoformat"):  # date / datetime
        return v.isoformat()
    return str(v)


# ── Persistence ───────────────────────────────────────────────────────

async def _upsert_columns(source_id: str, rows: Sequence[Dict[str, Any]]) -> Dict[str, int]:
    """Replace-by-(source_id, table_name) so re-uploads of the same file
    don't accumulate stale rows. Idempotent."""
    table_names = sorted({r["table_name"] for r in rows})
    factory = get_session_factory()
    async with factory() as sess:
        # Delete-then-insert is the dialect-portable upsert: SQLite,
        # Postgres, CockroachDB, and YugabyteDB all support DELETE + INSERT
        # in one transaction; ``ON CONFLICT`` syntax differs across them.
        for tname in table_names:
            await sess.execute(
                delete(SchemaColumn).where(
                    SchemaColumn.source_id == source_id,
                    SchemaColumn.table_name == tname,
                )
            )
        now = datetime.now(timezone.utc)
        sess.add_all([
            SchemaColumn(
                source_id=source_id,
                table_name=r["table_name"],
                column_name=r["column_name"],
                column_name_lower=r["column_name"].lower(),
                data_type=r["data_type"],
                is_nullable=bool(r["is_nullable"]),
                ordinal_position=r.get("ordinal_position"),
                sample_values=list(r.get("sample_values", []))[:_SAMPLE_LIMIT],
                created_at=now,
                updated_at=now,
            )
            for r in rows
        ])
        await sess.commit()
    return {"tables": len(table_names), "columns": len(rows)}
