"""
Smart Data Loader
=================
Handles headerless CSVs, infers column names from data patterns,
builds rich schema context for LLM, and detects cross-table relationships.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from shared.cache import schema_cache
from shared.sql_identifiers import quote_identifier, quote_literal

logger = logging.getLogger("aura.data_utils")

SCHEMA_CACHE_PREFIX = "schema:ctx:"


# Inferred headers are a pure function of file content, so we persist them in a
# sidecar keyed by (mtime_ns, size). This survives process restarts and the
# in-memory schema_cache's TTL expiry, so the (rate-limited, ~seconds-each) LLM
# header-inference call is made exactly once per file version — never again
# unless the file actually changes. Without this, every cold start re-inferred
# headers for every headerless file, which both dominated first-request latency
# and exhausted free-tier LLM quotas before the user's question was even seen.
_HEADER_CACHE_DIRNAME = ".aura_header_cache"


def _header_cache_path(file_path: str) -> Path:
    p = Path(file_path)
    # Sidecars live in a sibling subdir (not a sibling file) so the non-recursive
    # upload-dir scan in build_schema_context never mistakes them for data files.
    return p.parent / _HEADER_CACHE_DIRNAME / f"{p.name}.json"


def _load_cached_headers(
    file_path: str, stat: os.stat_result, n_cols: int
) -> Optional[List[str]]:
    """Return persisted inferred headers iff they match this file's (mtime, size)
    and column count; otherwise None (forcing a fresh inference)."""
    cache_path = _header_cache_path(file_path)
    try:
        with open(cache_path, encoding="utf-8") as fh:
            payload = json.load(fh)
    except (OSError, ValueError):
        return None
    if (
        payload.get("mtime_ns") != stat.st_mtime_ns
        or payload.get("size") != stat.st_size
    ):
        return None
    headers = payload.get("headers")
    if isinstance(headers, list) and len(headers) == n_cols:
        return [str(h) for h in headers]
    return None


def _store_cached_headers(
    file_path: str, stat: os.stat_result, headers: List[str]
) -> None:
    cache_path = _header_cache_path(file_path)
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = cache_path.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(
                {
                    "mtime_ns": stat.st_mtime_ns,
                    "size": stat.st_size,
                    "headers": headers,
                },
                fh,
            )
        os.replace(tmp, cache_path)  # atomic publish
    except OSError as exc:
        # Caching is best-effort; a read-only data dir must not break loading.
        logger.debug("Could not persist header cache for %s: %s", file_path, exc)


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
    qtable = quote_identifier(table_name)

    # Step 1: Load with read_csv_auto to let DuckDB detect
    conn.execute(
        f"CREATE OR REPLACE TABLE {qtable} AS "
        f"SELECT * FROM read_csv_auto({quote_literal(file_path_str)})"
    )

    # Get what DuckDB detected
    cols = conn.execute(f"DESCRIBE {qtable}").fetchall()
    col_names = [c[0] for c in cols]
    col_types = [c[1] for c in cols]
    row_count_result = conn.execute(f"SELECT COUNT(*) FROM {qtable}").fetchone()
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
        try:
            file_stat = Path(file_path).stat()
        except OSError:
            file_stat = None

        inferred_names = (
            _load_cached_headers(file_path, file_stat, len(col_names))
            if file_stat is not None
            else None
        )

        if inferred_names:
            logger.info(
                "File '%s' has no headers — using cached inferred names", file_name
            )
        else:
            logger.info("File '%s' has no headers — inferring column names", file_name)

            # Get sample data for inference
            sample = conn.execute(f"SELECT * FROM {qtable} LIMIT 20").fetchall()

            # Try LLM inference first
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

            if file_stat is not None:
                _store_cached_headers(file_path, file_stat, inferred_names)

        # Rename columns in the table
        for old_name, new_name in zip(col_names, inferred_names):
            if old_name != new_name:
                conn.execute(
                    f"ALTER TABLE {qtable} RENAME COLUMN "
                    f"{quote_identifier(old_name)} TO {quote_identifier(new_name)}"
                )

        col_names = inferred_names
        result["has_headers"] = False
        result["headers_inferred"] = True

    # Get final column info
    final_cols = conn.execute(f"DESCRIBE {qtable}").fetchall()
    result["columns"] = [{"name": c[0], "type": c[1]} for c in final_cols]

    # Sample data with actual column names
    sample_rows = conn.execute(f"SELECT * FROM {qtable} LIMIT 5").fetchall()
    final_col_names = [c[0] for c in final_cols]
    result["sample_data"] = [
        {col: _serialize(val) for col, val in zip(final_col_names, row)}
        for row in sample_rows
    ]

    return result


EXCEL_EXTENSIONS = (".xlsx",)
# Sentinel stored in a schema-cache loader recipe: DuckDB has no built-in reader
# for Excel (its excel extension is a runtime download), so the file is read with
# pandas/openpyxl instead of `read_fn(uri)`.
EXCEL_READ_FN = "__excel__"
# Ceilings for one workbook (BUG-180); generous for real spreadsheets, hostile to bombs.
MAX_EXCEL_UNCOMPRESSED_BYTES = 200 * 1024 * 1024
MAX_EXCEL_ROWS = 1_000_000


def _load_excel_table(conn: Any, file_path: str, qtable: str) -> None:
    """Load the first sheet of an .xlsx workbook into DuckDB table *qtable* (BUG-146).

    Only local paths: a remote (s3://...) object can't be opened by openpyxl here,
    and failing loudly beats the old behaviour of parsing the binary as CSV.
    """
    import zipfile

    import pandas as pd

    if "://" in str(file_path):
        raise ValueError("Excel files are only supported on local storage; convert to CSV or Parquet.")
    # Bound the work before parsing (BUG-180). An .xlsx is a zip: the upload gate sees only
    # its compressed size, so a small file can expand enormously, and openpyxl parses in pure
    # Python inside a single-worker process.
    try:
        with zipfile.ZipFile(file_path) as zf:
            expanded = sum(i.file_size for i in zf.infolist())
    except zipfile.BadZipFile as exc:
        raise ValueError("Not a valid .xlsx workbook.") from exc
    if expanded > MAX_EXCEL_UNCOMPRESSED_BYTES:
        raise ValueError(
            f"Workbook expands to {expanded // (1024 * 1024)}MB, over the "
            f"{MAX_EXCEL_UNCOMPRESSED_BYTES // (1024 * 1024)}MB limit; export it as CSV or Parquet."
        )
    df = pd.read_excel(file_path, sheet_name=0, engine="openpyxl", nrows=MAX_EXCEL_ROWS + 1)
    if len(df) > MAX_EXCEL_ROWS:
        raise ValueError(f"The first sheet has more than {MAX_EXCEL_ROWS:,} rows; export it as CSV or Parquet.")
    df.columns = [str(c) for c in df.columns]
    conn.register("_aura_excel_tmp", df)
    try:
        conn.execute(f"CREATE OR REPLACE TABLE {qtable} AS SELECT * FROM _aura_excel_tmp")
    finally:
        conn.unregister("_aura_excel_tmp")


def _unique_table_name(filename: str, taken: Any) -> str:
    """Table name for an uploaded file, distinct from every name in ``taken`` (BUG-178).

    ``q1-sales.csv`` and ``q1_sales.csv`` (or ``sales.csv`` and ``sales.parquet``) both
    sanitise to one name, and the second load used to ``CREATE OR REPLACE`` the first
    silently. The first file keeps the plain name; a colliding one gets its extension
    and then a counter appended. Listing order is sorted, so the result is stable.
    """
    stem, ext = os.path.splitext(filename)
    base = re.sub(r"[^A-Za-z0-9_]", "_", stem)
    if base not in taken:
        return base
    candidate = f"{base}_{ext.lstrip('.').lower()}" if ext else base
    n = 2
    while candidate in taken:
        candidate = f"{base}_{n}"
        n += 1
    return candidate


def smart_load_file(
    conn: Any,
    file_path: str,
    table_name: str,
    use_llm: bool = True,
) -> Dict[str, Any]:
    """Smart loader for any file type (CSV, Parquet, JSON, Excel .xlsx)."""
    ext = Path(file_path).suffix.lower()

    if ext == ".csv":
        return smart_load_csv(conn, file_path, table_name, use_llm=use_llm)
    else:
        # Parquet, JSON — these always have proper column names
        file_path_str = str(file_path).replace("\\", "/")
        read_fn = {".parquet": "read_parquet", ".json": "read_json_auto"}.get(ext, "read_csv_auto")
        qtable = quote_identifier(table_name)
        if ext in EXCEL_EXTENSIONS:
            _load_excel_table(conn, file_path_str, qtable)
        else:
            conn.execute(
                f"CREATE OR REPLACE TABLE {qtable} AS "
                f"SELECT * FROM {read_fn}({quote_literal(file_path_str)})"
            )
        cols = conn.execute(f"DESCRIBE {qtable}").fetchall()
        row_count = conn.execute(f"SELECT COUNT(*) FROM {qtable}").fetchone()[0]
        sample_rows = conn.execute(f"SELECT * FROM {qtable} LIMIT 5").fetchall()
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
    tenant: str,
    use_llm: bool = True,
) -> Dict[str, Any]:
    """
    Load ALL files for *tenant* from the storage backend, build rich schema context.

    Returns:
        {
            "tables": {table_name: {columns, row_count, sample_data, ...}},
            "relationships": [...],
            "context_text": "formatted string for LLM",
        }
    """
    from shared.storage import get_storage_backend
    backend = get_storage_backend()
    tables: Dict[str, Dict] = {}

    for obj in backend.list(tenant):
        ext = os.path.splitext(obj.name)[1].lower()
        if ext not in (".csv", ".parquet", ".json", *EXCEL_EXTENSIONS):
            continue
        table_name = _unique_table_name(obj.name, tables)
        try:
            info = smart_load_file(conn, obj.duckdb_uri, table_name, use_llm=use_llm)
            tables[table_name] = info
        except Exception as e:
            logger.warning("Failed to load %s: %s", obj.name, e)

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
                            qc1, qt1 = quote_identifier(c1_name), quote_identifier(t1)
                            qc2, qt2 = quote_identifier(c2_name), quote_identifier(t2)
                            overlap = conn.execute(f"""
                                SELECT COUNT(*) FROM (
                                    SELECT DISTINCT {qc1} FROM {qt1}
                                    INTERSECT
                                    SELECT DISTINCT {qc2} FROM {qt2}
                                ) sub
                            """).fetchone()[0]
                            if overlap > 0:
                                # Determine which is PK (more unique values usually)
                                cnt1 = conn.execute(
                                    f"SELECT COUNT(DISTINCT {qc1}) FROM {qt1}"
                                ).fetchone()[0]
                                cnt2 = conn.execute(
                                    f"SELECT COUNT(DISTINCT {qc2}) FROM {qt2}"
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

def select_tables_within_budget(
    tables: Dict[str, Dict],
    relationships: List[Dict],
    message: str,
    budget_chars: int,
    focus: Optional[str] = None,
) -> Tuple[Dict[str, Dict], List[Dict], str]:
    """The tables most relevant to ``message`` whose formatted schema fits the budget.

    BUG-444: when every table's schema was over the budget and the message named no
    table, the whole context went to the model anyway -- 56,037 tokens against the
    8,000-token request cap for an account with 72 uploads -- so every data question
    failed. Tables are ranked by how many of the message's words appear in the table
    or column names (``focus`` and its related tables first) and added while the
    formatted text stays within ``budget_chars``. At least one table is always kept.
    Returns ``(tables, relationships, context_text)``.
    """
    words = {w for w in re.findall(r"[a-z0-9]+", message.lower()) if len(w) >= 3}

    def score(name: str) -> int:
        cols = " ".join(str(c.get("name", "")) for c in tables[name].get("columns", []))
        haystack = f"{name} {cols}".lower()
        return sum(1 for w in words if w in haystack)

    first = []
    if focus in tables:
        first.append(focus)
        for r in relationships:
            if focus in (r.get("from_table"), r.get("to_table")):
                first.extend(t for t in (r.get("from_table"), r.get("to_table")) if t in tables)
    rest = sorted((n for n in tables if n not in first), key=score, reverse=True)

    chosen: Dict[str, Dict] = {}
    text = ""
    for name in dict.fromkeys(first + rest):
        trial = {**chosen, name: tables[name]}
        rels = [r for r in relationships if r.get("from_table") in trial and r.get("to_table") in trial]
        trial_text = _format_context_for_llm(trial, rels)
        if chosen and len(trial_text) > budget_chars:
            continue
        chosen, text = trial, trial_text
    rels = [r for r in relationships if r.get("from_table") in chosen and r.get("to_table") in chosen]
    return chosen, rels, text


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
    **{e: EXCEL_READ_FN for e in EXCEL_EXTENSIONS},
}


def _signature_for_tenant(tenant: str) -> str:
    """Stable fingerprint over (name, fingerprint) of every data file for *tenant*.

    Empty string when no files are present (signals callers to skip cache
    entirely instead of caching an empty result keyed on nothing).
    """
    from shared.storage import get_storage_backend
    parts = [f"{o.name}|{o.fingerprint}" for o in get_storage_backend().list(tenant)]
    if not parts:
        return ""
    return hashlib.sha256("\n".join(sorted(parts)).encode("utf-8")).hexdigest()[:32]


def _replay_tables(conn: Any, loaders: List[Dict[str, Any]]) -> None:
    """Re-create cached tables on a fresh DuckDB connection."""
    for loader in loaders:
        uri = loader["duckdb_uri"]
        table_name = loader["table_name"]
        read_fn = loader["read_fn"]
        try:
            qtable = quote_identifier(table_name)
            if read_fn == EXCEL_READ_FN:
                _load_excel_table(conn, uri, qtable)
            else:
                conn.execute(
                    f"CREATE OR REPLACE TABLE {qtable} AS "
                    f"SELECT * FROM {read_fn}({quote_literal(uri)})"
                )
            for old, new in loader.get("renames", []):
                if old != new:
                    conn.execute(
                        f"ALTER TABLE {qtable} RENAME COLUMN "
                        f"{quote_identifier(old)} TO {quote_identifier(new)}"
                    )
        except Exception as e:
            logger.warning("Cache replay failed for %s: %s", table_name, e)


# ─── Catalog: profile each file once, load only what a question needs ─────────
#
# The schema of an upload is worked out once and kept: in memory, and for local
# storage in a sidecar next to the file, so it also survives a restart. A request
# then reads the catalog and materialises only the tables it is going to query.
# Before this, every request loaded every one of the tenant's files into memory,
# and one new upload re-profiled all of them (header inference by LLM included).

_CATALOG_DIRNAME = ".aura_catalog"
# Bump when the shape of a stored entry changes; older entries are then rebuilt.
# 2: tables are loaded from a Parquet copy made while profiling.
_CATALOG_VERSION = 2
_RELATIONSHIPS_FILE = "_relationships.json"


def _catalog_dir(duckdb_uri: str) -> Optional[Path]:
    """Sidecar directory for a local upload; None for remote storage (s3://...)."""
    if "://" in duckdb_uri:
        return None
    # A sibling subdirectory, like the header cache, so the non-recursive upload
    # scan never mistakes a sidecar for a data file.
    return Path(duckdb_uri).parent / _CATALOG_DIRNAME


def _read_json(path: Optional[Path]) -> Optional[Any]:
    if path is None:
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def _write_json(path: Optional[Path], payload: Any) -> None:
    """Best effort: a catalog that cannot be written is rebuilt next time."""
    if path is None:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, default=str)
        os.replace(tmp, path)
    except OSError as exc:
        logger.debug("catalog sidecar not written (%s): %s", path, exc)


def _stored_entry(obj: Any) -> Optional[Dict[str, Any]]:
    d = _catalog_dir(obj.duckdb_uri)
    entry = _read_json(d / f"{obj.name}.json" if d else None)
    if (
        isinstance(entry, dict)
        and entry.get("version") == _CATALOG_VERSION
        and entry.get("fingerprint") == obj.fingerprint
    ):
        return entry
    return None


def _profile_file(conn: Any, obj: Any, table_name: str, read_fn: str, use_llm: bool) -> Tuple[Dict, Dict]:
    """Load one file into *conn* and describe it. Returns ``(info, loader)``."""
    info = smart_load_file(conn, obj.duckdb_uri, table_name, use_llm=use_llm)
    renames: List[Tuple[str, str]] = []
    if info.get("headers_inferred"):
        sniff = conn.execute(
            f"DESCRIBE SELECT * FROM {read_fn}({quote_literal(obj.duckdb_uri)})"
        ).fetchall()
        original_cols = [r[0] for r in sniff]
        final_cols = [c["name"] for c in info["columns"]]
        if len(original_cols) == len(final_cols):
            renames = list(zip(original_cols, final_cols))
    loader = {"table_name": table_name, "duckdb_uri": obj.duckdb_uri, "read_fn": read_fn, "renames": renames}
    return info, _parquet_loader(conn, obj, table_name) or loader


def _parquet_loader(conn: Any, obj: Any, table_name: str) -> Optional[Dict[str, Any]]:
    """Write the loaded table to a Parquet copy beside the upload and return a loader
    that reads it, or None (keep reading the original) for remote storage or on error.

    Parquet is columnar and compressed: a request loads it far faster than re-parsing
    a CSV, and an .xlsx no longer goes through pandas on every request. The table is
    written after header inference, so the copy already carries the final names.
    """
    d = _catalog_dir(obj.duckdb_uri)
    if d is None:
        return None
    target = d / f"{obj.name}.parquet"
    tmp = d / f"{obj.name}.parquet.tmp"
    try:
        d.mkdir(parents=True, exist_ok=True)
        conn.execute(
            f"COPY {quote_identifier(table_name)} TO {quote_literal(tmp.as_posix())} "
            "(FORMAT PARQUET, COMPRESSION ZSTD)"
        )
        os.replace(tmp, target)
    except Exception as exc:
        logger.debug("parquet copy skipped for %s: %s", obj.name, exc)
        return None
    return {"table_name": table_name, "duckdb_uri": target.as_posix(), "read_fn": "read_parquet", "renames": []}


def _prune_sidecars(catalog_dir: Path, live_names: set) -> None:
    """Remove catalog entries and Parquet copies of files that are gone, so a
    deleted upload does not keep costing disk."""
    try:
        entries = list(catalog_dir.iterdir())
    except OSError:
        return
    for f in entries:
        name = f.name
        for suffix in (".json", ".parquet", ".parquet.tmp", ".json.tmp"):
            if name.endswith(suffix):
                base = name[: -len(suffix)]
                if base and base not in live_names and not name.startswith("_relationships"):
                    try:
                        f.unlink()
                    except OSError:
                        pass
                break


def _might_relate(a: Dict, b: Dict) -> bool:
    return any(
        _columns_might_relate(c1["name"], c2["name"], c1["type"], c2["type"])
        for c1 in a["columns"] for c2 in b["columns"]
    )


def _build_catalog(tenant: str, use_llm: bool) -> Dict[str, Any]:
    """Describe every data file of *tenant*, reusing each stored entry whose file is
    unchanged. Only new or changed files are loaded and profiled, and only table
    pairs not seen before are probed for relationships, on a scratch connection."""
    from shared.duckdb_factory import new_connection
    from shared.storage import get_storage_backend

    backend = get_storage_backend()
    scratch = new_connection()
    try:
        tables: Dict[str, Dict] = {}
        loaders: Dict[str, Dict] = {}
        keys: Dict[str, str] = {}  # table name -> "name|fingerprint" of its file
        loaded: set = set()
        rel_dirs: set = set()
        for obj in backend.list(tenant):
            ext = os.path.splitext(obj.name)[1].lower()
            read_fn = _READ_FN_BY_EXT.get(ext)
            if not read_fn:
                continue
            table_name = _unique_table_name(obj.name, tables)
            entry = _stored_entry(obj)
            try:
                if entry is not None:
                    info, loader = entry["info"], entry["loader"]
                else:
                    info, loader = _profile_file(scratch, obj, table_name, read_fn, use_llm)
                    loaded.add(table_name)
                    d = _catalog_dir(obj.duckdb_uri)
                    _write_json(d / f"{obj.name}.json" if d else None, {
                        "version": _CATALOG_VERSION, "fingerprint": obj.fingerprint,
                        "info": info, "loader": loader,
                    })
            except Exception as e:
                logger.warning("Failed to load %s: %s", obj.name, e)
                continue
            # The table name depends on the other files' names, so it is set here
            # rather than trusted from the stored entry.
            tables[table_name] = {**info, "table_name": table_name}
            loaders[table_name] = {**loader, "table_name": table_name}
            keys[table_name] = f"{obj.name}|{obj.fingerprint}"
            d = _catalog_dir(obj.duckdb_uri)
            if d is not None:
                rel_dirs.add(d)

        live_names = {k.split("|", 1)[0] for k in keys.values()}
        for d in rel_dirs:
            _prune_sidecars(d, live_names)

        # One relationship cache per tenant directory (there is exactly one locally).
        rel_path = next(iter(rel_dirs)) / _RELATIONSHIPS_FILE if len(rel_dirs) == 1 else None
        stored = _read_json(rel_path)
        rel_cache: Dict[str, List[Dict]] = stored if isinstance(stored, dict) else {}
        fresh_cache: Dict[str, List[Dict]] = {}
        relationships: List[Dict] = []
        names = list(tables)
        for a_i, t1 in enumerate(names):
            for t2 in names[a_i + 1:]:
                if not _might_relate(tables[t1], tables[t2]):
                    continue
                pair_key = f"{t1}={keys[t1]}||{t2}={keys[t2]}"
                if pair_key not in rel_cache:
                    for t in (t1, t2):
                        if t not in loaded:
                            _replay_tables(scratch, [loaders[t]])
                            loaded.add(t)
                    rel_cache[pair_key] = detect_relationships(scratch, {t1: tables[t1], t2: tables[t2]})
                fresh_cache[pair_key] = rel_cache[pair_key]
                relationships.extend(rel_cache[pair_key])
        _write_json(rel_path, fresh_cache)  # pairs of deleted or changed files drop out
    finally:
        scratch.close()

    return {
        "tables": tables,
        "relationships": relationships,
        "context_text": _format_context_for_llm(tables, relationships),
        "loaders": list(loaders.values()),
    }


async def load_catalog(tenant: Optional[str], use_llm: bool = True) -> Dict[str, Any]:
    """The tenant's catalog: ``{tables, relationships, context_text, loaders}``.

    Loads no data into any caller's connection; use ``materialize_tables`` for the
    tables a query needs. Cheap when nothing changed (in-memory hit), and after a
    restart or a new upload only new or changed files are profiled.
    """
    effective_tenant: str = tenant if tenant is not None else "default"
    # backend.list() is blocking I/O (a boto3 paginator on S3): keep it off the loop (BUG-177).
    sig = await asyncio.to_thread(_signature_for_tenant, effective_tenant)
    if not sig:
        return {"tables": {}, "relationships": [], "context_text": "", "loaders": []}
    cache_key = f"{SCHEMA_CACHE_PREFIX}{effective_tenant}:{sig}"
    cached = await schema_cache.get(cache_key)
    if cached:
        return cached
    catalog = await asyncio.to_thread(_build_catalog, effective_tenant, use_llm)
    await schema_cache.set(cache_key, catalog)
    return catalog


async def materialize_tables(conn: Any, catalog: Dict[str, Any], names: Optional[Any] = None) -> None:
    """Load the named tables of *catalog* into *conn* (all of them when *names* is
    None). Must run before ``lock_down_connection``, which cuts off file access."""
    wanted = None if names is None else set(names)
    loaders = [ld for ld in catalog.get("loaders", []) if wanted is None or ld["table_name"] in wanted]
    if loaders:
        await asyncio.to_thread(_replay_tables, conn, loaders)


async def build_schema_context_cached(
    conn: Any,
    tenant: Optional[str],
    use_llm: bool = True,
) -> Dict[str, Any]:
    """The tenant's schema context, with every table loaded into *conn*.

    ``tenant`` is the caller's org id (from the verified JWT via
    ``_request_tenant``); ``None`` maps to the 'default' tenant bucket. Callers
    that query only some tables should use ``load_catalog`` and
    ``materialize_tables`` instead and load just those.
    """
    catalog = await load_catalog(tenant, use_llm=use_llm)
    await materialize_tables(conn, catalog)
    return {
        "tables": catalog["tables"],
        "relationships": catalog["relationships"],
        "context_text": catalog["context_text"],
    }


async def invalidate_schema_cache() -> None:
    """Drop every cached schema context. Call after any uploaded-file change."""
    await schema_cache.clear_prefix(SCHEMA_CACHE_PREFIX)
