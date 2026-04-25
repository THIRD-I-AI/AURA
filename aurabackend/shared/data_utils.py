"""
Smart Data Loader
=================
Handles headerless CSVs, infers column names from data patterns,
builds rich schema context for LLM, and detects cross-table relationships.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from shared.cache import schema_cache

logger = logging.getLogger("aura.data_utils")

SCHEMA_CACHE_PREFIX = "schema:ctx:"

UPLOAD_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "uploads")


# ─── Header Detection ────────────────────────────────────────────────────────

def _looks_like_header(first_row: tuple, second_row: tuple) -> bool:
    """
    Heuristic: check if the first row looks like column headers.
    Headers are typically all strings, non-numeric, and look like identifiers.
    """
    if not first_row or not second_row:
        return True  # default to has-headers

    header_signals = 0
    data_signals = 0

    for val in first_row:
        s = str(val).strip()
        # Pure numeric → likely data, not a header
        try:
            float(s.replace(",", ""))
            data_signals += 1
            continue
        except (ValueError, AttributeError):
            pass
        # Looks like an identifier (letters, underscores, short)
        if re.match(r'^[a-zA-Z_][a-zA-Z0-9_ ]{0,50}$', s):
            header_signals += 1
        # Contains @ or looks like email → data
        elif '@' in s:
            data_signals += 1
        # Long string with spaces → probably data
        elif len(s) > 30:
            data_signals += 1
        else:
            header_signals += 1

    # If most values look like identifiers, it's likely a header row
    return header_signals > data_signals


def _infer_column_name(values: list, col_idx: int) -> str:
    """Infer a meaningful column name from sample values."""
    sample = [str(v).strip() for v in values if v is not None and str(v).strip()][:20]
    if not sample:
        return f"column_{col_idx}"

    # Check patterns
    # All integers starting from 1, increasing → likely an ID
    all_int = all(re.match(r'^\d+$', v) for v in sample)

    # Email pattern
    if all('@' in v and '.' in v for v in sample):
        return "email"

    # Phone pattern (digits, dashes, parens, spaces, plus)
    if all(re.match(r'^[\d\s\-\+\(\)\.]{7,20}$', v) for v in sample):
        return "phone"

    # Date/datetime pattern
    if all(re.match(r'^\d{4}[-/]\d{1,2}[-/]\d{1,2}', v) for v in sample):
        return "date" if col_idx == 0 else "order_date"

    # Timestamp with time component
    if all(re.match(r'^\d{4}[-/]\d{1,2}[-/]\d{1,2}\s+\d{1,2}:', v) for v in sample):
        return "timestamp" if col_idx == 0 else "order_date"

    # All integers
    if all_int:
        int_vals = [int(v) for v in sample]
        # First column and sequential → ID
        if col_idx == 0:
            return "id"
        # Small values (1-1000) → could be quantity or FK
        max_val = max(int_vals)
        if max_val <= 100:
            return f"value_{col_idx}"
        return f"id_{col_idx}"

    # All floats → numeric metric
    if all(re.match(r'^-?\d+\.?\d*$', v) for v in sample):
        return f"amount_{col_idx}"

    # Short capitalized strings → names
    if all(len(v) < 30 and v[0].isupper() for v in sample if v):
        if col_idx == 1:
            return "first_name"
        elif col_idx == 2:
            return "last_name"
        return f"name_{col_idx}"

    return f"column_{col_idx}"


# ─── LLM-based Header Inference ──────────────────────────────────────────────

def infer_headers_with_llm(
    file_name: str,
    col_types: List[str],
    sample_rows: List[tuple],
) -> Optional[List[str]]:
    """Use LLM to infer meaningful column names from sample data."""
    try:
        from shared.llm_provider import get_llm
        llm = get_llm()
        if not llm.is_available():
            return None
    except Exception:
        return None

    # Build sample display
    sample_text = ""
    for i, row in enumerate(sample_rows[:5]):
        sample_text += f"  Row {i+1}: {list(row)}\n"

    prompt = f"""Analyze this CSV data and infer the BEST column names.

File name: {file_name}
Number of columns: {len(col_types)}
Column types detected: {col_types}

Sample data (first 5 rows):
{sample_text}

Based on the data patterns, file name, and types, give me the BEST column names.
Return ONLY a JSON array of strings with exactly {len(col_types)} column names.
Use snake_case. Be specific (e.g., "customer_id" not "id", "order_date" not "date").
No explanation, just the JSON array."""

    try:
        result = llm.generate_json(prompt)
        if isinstance(result, list) and len(result) == len(col_types):
            # Validate: all strings, reasonable names
            names = [str(n).strip().replace(" ", "_").lower() for n in result]
            if all(re.match(r'^[a-z_][a-z0-9_]*$', n) for n in names):
                logger.info("LLM inferred headers for %s: %s", file_name, names)
                return names
    except Exception as e:
        logger.warning("LLM header inference failed: %s", e)

    return None


# ─── Smart CSV Loading ────────────────────────────────────────────────────────

def smart_load_csv(
    conn: Any,
    file_path: str,
    table_name: str,
    use_llm: bool = True,
) -> Dict[str, Any]:
    """
    Load a CSV file into DuckDB with smart header detection and inference.

    Returns:
        {
            "table_name": str,
            "columns": [{"name": str, "type": str}],
            "has_headers": bool,
            "headers_inferred": bool,
            "row_count": int,
            "sample_data": [...],
        }
    """
    file_path_str = str(file_path).replace("\\", "/")
    file_name = Path(file_path).name

    # Step 1: Load with read_csv_auto to let DuckDB detect
    conn.execute(
        f'CREATE OR REPLACE TABLE "{table_name}" AS '
        f"SELECT * FROM read_csv_auto('{file_path_str}')"
    )

    # Get what DuckDB detected
    cols = conn.execute(f'DESCRIBE "{table_name}"').fetchall()
    col_names = [c[0] for c in cols]
    col_types = [c[1] for c in cols]
    row_count_result = conn.execute(f'SELECT COUNT(*) FROM "{table_name}"').fetchone()
    row_count = row_count_result[0] if row_count_result else 0

    # Check if headers are generic (column0, column1, ...)
    has_generic_headers = all(re.match(r'^column\d+$', c) for c in col_names)

    result = {
        "table_name": table_name,
        "has_headers": not has_generic_headers,
        "headers_inferred": False,
        "row_count": row_count,
    }

    if has_generic_headers:
        logger.info("File '%s' has no headers — inferring column names", file_name)

        # Get sample data for inference
        sample = conn.execute(f'SELECT * FROM "{table_name}" LIMIT 20').fetchall()

        # Try LLM inference first
        inferred_names = None
        if use_llm:
            inferred_names = infer_headers_with_llm(file_name, col_types, sample)

        # Fallback to heuristic inference
        if not inferred_names:
            # Transpose sample for per-column analysis
            inferred_names = []
            for idx in range(len(col_names)):
                col_values = [row[idx] for row in sample]
                inferred_names.append(_infer_column_name(col_values, idx))

            # De-duplicate names
            seen = {}
            for i, name in enumerate(inferred_names):
                if name in seen:
                    seen[name] += 1
                    inferred_names[i] = f"{name}_{seen[name]}"
                else:
                    seen[name] = 0

        # Rename columns in the table
        for old_name, new_name in zip(col_names, inferred_names):
            if old_name != new_name:
                conn.execute(
                    f'ALTER TABLE "{table_name}" RENAME COLUMN "{old_name}" TO "{new_name}"'
                )

        col_names = inferred_names
        result["has_headers"] = False
        result["headers_inferred"] = True

    # Get final column info
    final_cols = conn.execute(f'DESCRIBE "{table_name}"').fetchall()
    result["columns"] = [{"name": c[0], "type": c[1]} for c in final_cols]

    # Sample data with actual column names
    sample_rows = conn.execute(f'SELECT * FROM "{table_name}" LIMIT 5').fetchall()
    final_col_names = [c[0] for c in final_cols]
    result["sample_data"] = [
        {col: _serialize(val) for col, val in zip(final_col_names, row)}
        for row in sample_rows
    ]

    return result


def smart_load_file(
    conn: Any,
    file_path: str,
    table_name: str,
    use_llm: bool = True,
) -> Dict[str, Any]:
    """Smart loader for any file type (CSV, Parquet, JSON)."""
    ext = Path(file_path).suffix.lower()

    if ext == ".csv":
        return smart_load_csv(conn, file_path, table_name, use_llm=use_llm)
    else:
        # Parquet, JSON — these always have proper column names
        file_path_str = str(file_path).replace("\\", "/")
        read_fn = {".parquet": "read_parquet", ".json": "read_json_auto"}.get(ext, "read_csv_auto")
        conn.execute(
            f'CREATE OR REPLACE TABLE "{table_name}" AS '
            f"SELECT * FROM {read_fn}('{file_path_str}')"
        )
        cols = conn.execute(f'DESCRIBE "{table_name}"').fetchall()
        row_count = conn.execute(f'SELECT COUNT(*) FROM "{table_name}"').fetchone()[0]
        sample_rows = conn.execute(f'SELECT * FROM "{table_name}" LIMIT 5').fetchall()
        col_names = [c[0] for c in cols]

        return {
            "table_name": table_name,
            "columns": [{"name": c[0], "type": c[1]} for c in cols],
            "has_headers": True,
            "headers_inferred": False,
            "row_count": row_count,
            "sample_data": [
                {col: _serialize(val) for col, val in zip(col_names, row)}
                for row in sample_rows
            ],
        }


# ─── Multi-Table Context Builder ─────────────────────────────────────────────

def build_schema_context(
    conn: Any,
    upload_dirs: List[Path],
    use_llm: bool = True,
) -> Dict[str, Any]:
    """
    Load ALL files from upload directories, build rich schema context.

    Returns:
        {
            "tables": {table_name: {columns, row_count, sample_data, ...}},
            "relationships": [...],
            "context_text": "formatted string for LLM",
        }
    """
    tables: Dict[str, Dict] = {}

    for upload_dir in upload_dirs:
        if not upload_dir.exists():
            continue
        for data_file in sorted(upload_dir.iterdir()):
            ext = data_file.suffix.lower()
            if ext not in (".csv", ".parquet", ".json"):
                continue
            table_name = re.sub(r"[^A-Za-z0-9_]", "_", data_file.stem)
            try:
                info = smart_load_file(conn, str(data_file), table_name, use_llm=use_llm)
                tables[table_name] = info
            except Exception as e:
                logger.warning("Failed to load %s: %s", data_file.name, e)

    # Detect relationships
    relationships = detect_relationships(conn, tables)

    # Build formatted context string for LLM
    context_text = _format_context_for_llm(tables, relationships)

    return {
        "tables": tables,
        "relationships": relationships,
        "context_text": context_text,
    }


# ─── Relationship Detection ──────────────────────────────────────────────────

def detect_relationships(
    conn: Any,
    tables: Dict[str, Dict],
) -> List[Dict[str, str]]:
    """
    Detect foreign key relationships between tables by matching column names
    and checking value overlap.
    """
    relationships = []
    table_names = list(tables.keys())

    for i, t1 in enumerate(table_names):
        cols1 = {c["name"]: c["type"] for c in tables[t1]["columns"]}
        for j, t2 in enumerate(table_names):
            if i >= j:
                continue
            cols2 = {c["name"]: c["type"] for c in tables[t2]["columns"]}

            # Check for columns with matching names that contain "id"
            for c1_name, c1_type in cols1.items():
                for c2_name, c2_type in cols2.items():
                    if _columns_might_relate(c1_name, c2_name, c1_type, c2_type):
                        # Verify with value overlap
                        try:
                            overlap = conn.execute(f"""
                                SELECT COUNT(*) FROM (
                                    SELECT DISTINCT "{c1_name}" FROM "{t1}"
                                    INTERSECT
                                    SELECT DISTINCT "{c2_name}" FROM "{t2}"
                                ) sub
                            """).fetchone()[0]
                            if overlap > 0:
                                # Determine which is PK (more unique values usually)
                                cnt1 = conn.execute(
                                    f'SELECT COUNT(DISTINCT "{c1_name}") FROM "{t1}"'
                                ).fetchone()[0]
                                cnt2 = conn.execute(
                                    f'SELECT COUNT(DISTINCT "{c2_name}") FROM "{t2}"'
                                ).fetchone()[0]

                                if cnt1 >= cnt2:
                                    pk_table, pk_col = t1, c1_name
                                    fk_table, fk_col = t2, c2_name
                                else:
                                    pk_table, pk_col = t2, c2_name
                                    fk_table, fk_col = t1, c1_name

                                relationships.append({
                                    "from_table": fk_table,
                                    "from_column": fk_col,
                                    "to_table": pk_table,
                                    "to_column": pk_col,
                                    "overlap_count": overlap,
                                    "type": "foreign_key",
                                })
                                logger.info(
                                    "Detected relationship: %s.%s -> %s.%s (%d overlapping values)",
                                    fk_table, fk_col, pk_table, pk_col, overlap,
                                )
                        except Exception:
                            pass

    return relationships


def _columns_might_relate(name1: str, name2: str, type1: str, type2: str) -> bool:
    """Check if two columns might have a foreign key relationship."""
    n1 = name1.lower()
    n2 = name2.lower()

    # Exact match on id-like columns
    if n1 == n2 and ("id" in n1 or "key" in n1 or "code" in n1):
        return True

    # One column is "{table}_id" and matches the other table's "id"
    # e.g., customer_id matches id in customer table
    if n1.endswith("_id") and n2 == "id":
        return True
    if n2.endswith("_id") and n1 == "id":
        return True

    # Both contain "id" and share a common prefix
    if "id" in n1 and "id" in n2:
        prefix1 = n1.replace("_id", "").replace("id", "")
        prefix2 = n2.replace("_id", "").replace("id", "")
        if prefix1 and prefix2 and (prefix1 in prefix2 or prefix2 in prefix1):
            return True

    # Compatible types check
    int_types = {"BIGINT", "INTEGER", "SMALLINT", "TINYINT", "INT"}
    if type1 in int_types and type2 in int_types:
        # Both numeric and names are similar
        if n1 == n2:
            return True

    return False


# ─── Formatting ──────────────────────────────────────────────────────────────

def _format_context_for_llm(
    tables: Dict[str, Dict],
    relationships: List[Dict],
) -> str:
    """Build a rich context string for LLM consumption."""
    parts = ["Available tables in DuckDB:\n"]

    for table_name, info in tables.items():
        cols = info["columns"]
        col_str = ", ".join(f'{c["name"]} ({c["type"]})' for c in cols)
        parts.append(f"Table: {table_name} — {info['row_count']} rows")
        parts.append(f"  Columns: {col_str}")

        # Add sample data
        if info.get("sample_data"):
            parts.append("  Sample data (first 3 rows):")
            for idx, row in enumerate(info["sample_data"][:3]):
                parts.append(f"    Row {idx+1}: {row}")

        if info.get("headers_inferred"):
            parts.append("  Note: Column names were inferred from data patterns (original file had no headers)")
        parts.append("")

    if relationships:
        parts.append("Table Relationships (detected):")
        for rel in relationships:
            parts.append(
                f"  {rel['from_table']}.{rel['from_column']} -> "
                f"{rel['to_table']}.{rel['to_column']} "
                f"(FK, {rel['overlap_count']} matching values)"
            )
        parts.append("")
        parts.append("You can JOIN these tables using the relationships above.")

    return "\n".join(parts)


def _serialize(val: Any) -> Any:
    """Serialize a value for JSON output."""
    if val is None:
        return None
    if isinstance(val, (int, float, bool, str)):
        return val
    return str(val)


# ─── Cached Schema Context ──────────────────────────────────────────────────

_READ_FN_BY_EXT = {
    ".csv": "read_csv_auto",
    ".parquet": "read_parquet",
    ".json": "read_json_auto",
}


def _signature_for_upload_dirs(upload_dirs: List[Path]) -> str:
    """Stable fingerprint over (path, mtime_ns, size) of every data file.

    Empty string when no files are present (signals callers to skip cache
    entirely instead of caching an empty result keyed on nothing).
    """
    parts: List[str] = []
    for upload_dir in upload_dirs:
        if not upload_dir.exists():
            continue
        for data_file in sorted(upload_dir.iterdir()):
            if data_file.suffix.lower() not in _READ_FN_BY_EXT:
                continue
            try:
                stat = data_file.stat()
            except OSError:
                continue
            parts.append(
                f"{data_file.resolve().as_posix()}|{stat.st_mtime_ns}|{stat.st_size}"
            )
    if not parts:
        return ""
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()[:32]


def _replay_tables(conn: Any, loaders: List[Dict[str, Any]]) -> None:
    """Re-create cached tables on a fresh DuckDB connection."""
    for loader in loaders:
        file_path = loader["file_path"]
        if not Path(file_path).exists():
            continue
        file_path_str = file_path.replace("\\", "/")
        table_name = loader["table_name"]
        read_fn = loader["read_fn"]
        try:
            conn.execute(
                f'CREATE OR REPLACE TABLE "{table_name}" AS '
                f"SELECT * FROM {read_fn}('{file_path_str}')"
            )
            for old, new in loader.get("renames", []):
                if old != new:
                    conn.execute(
                        f'ALTER TABLE "{table_name}" RENAME COLUMN "{old}" TO "{new}"'
                    )
        except Exception as e:
            logger.warning("Cache replay failed for %s: %s", table_name, e)


def _build_schema_context_with_recipe(
    conn: Any,
    upload_dirs: List[Path],
    use_llm: bool,
) -> Dict[str, Any]:
    """Same as build_schema_context but also returns a per-table loader recipe
    that can re-create the tables on a fresh DuckDB connection later."""
    tables: Dict[str, Dict] = {}
    loaders: List[Dict[str, Any]] = []

    for upload_dir in upload_dirs:
        if not upload_dir.exists():
            continue
        for data_file in sorted(upload_dir.iterdir()):
            ext = data_file.suffix.lower()
            read_fn = _READ_FN_BY_EXT.get(ext)
            if not read_fn:
                continue
            table_name = re.sub(r"[^A-Za-z0-9_]", "_", data_file.stem)
            try:
                info = smart_load_file(conn, str(data_file), table_name, use_llm=use_llm)
                tables[table_name] = info

                renames: List[Tuple[str, str]] = []
                if info.get("headers_inferred"):
                    file_path_str = str(data_file).replace("\\", "/")
                    sniff = conn.execute(
                        f"DESCRIBE SELECT * FROM {read_fn}('{file_path_str}')"
                    ).fetchall()
                    original_cols = [r[0] for r in sniff]
                    final_cols = [c["name"] for c in info["columns"]]
                    if len(original_cols) == len(final_cols):
                        renames = list(zip(original_cols, final_cols))

                loaders.append({
                    "table_name": table_name,
                    "file_path": str(data_file),
                    "read_fn": read_fn,
                    "renames": renames,
                })
            except Exception as e:
                logger.warning("Failed to load %s: %s", data_file.name, e)

    relationships = detect_relationships(conn, tables)
    context_text = _format_context_for_llm(tables, relationships)
    return {
        "tables": tables,
        "relationships": relationships,
        "context_text": context_text,
        "_loaders": loaders,
    }


async def build_schema_context_cached(
    conn: Any,
    upload_dirs: List[Path],
    use_llm: bool = True,
) -> Dict[str, Any]:
    """Cached + non-blocking variant of build_schema_context.

    Cache hit: replays the loader recipe on the fresh DuckDB ``conn`` and
    returns the cached schema dict.  Cache miss: runs the full discovery
    in a worker thread (LLM header inference + O(N²) relationship probes
    do not block the event loop) and stores the result.
    """
    sig = _signature_for_upload_dirs(upload_dirs)
    if not sig:
        return {"tables": {}, "relationships": [], "context_text": ""}

    cache_key = f"{SCHEMA_CACHE_PREFIX}{sig}"
    cached = await schema_cache.get(cache_key)
    if cached:
        await asyncio.to_thread(_replay_tables, conn, cached["loaders"])
        return {
            "tables": cached["tables"],
            "relationships": cached["relationships"],
            "context_text": cached["context_text"],
        }

    result = await asyncio.to_thread(
        _build_schema_context_with_recipe, conn, upload_dirs, use_llm
    )
    loaders = result.pop("_loaders")
    await schema_cache.set(cache_key, {
        "tables": result["tables"],
        "relationships": result["relationships"],
        "context_text": result["context_text"],
        "loaders": loaders,
    })
    return result


async def invalidate_schema_cache() -> None:
    """Drop every cached schema context. Call after any uploaded-file change."""
    await schema_cache.clear_prefix(SCHEMA_CACHE_PREFIX)
