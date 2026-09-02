"""
Semantic Builder
================
Generates a semantic model (dimension/measure classification plus a
human-readable description per column) from a dataset profile already
computed and persisted via ``metadata_store.repository.upsert_dataset_profile``.

Profile shape varies by writer:
  * Connector ingestion (``api_gateway/routers/connections.py``) stores
    ``{"schema": {col: type_str}, ...}``.
  * Column profiling (``shared.data_profile.profile_columns``) produces
    ``{col: {"dtype": ..., "distinct": ..., "null_ratio": ..., "sample": [...], "stats"?: {...}}}``
    — the vocabulary the rest of the app (viz/analysis prompts) already speaks.
This module normalizes either into the field dicts
``api_gateway.routers.pipelines.SemanticFieldPayload`` expects.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

_NUMERIC_TYPE_HINTS = (
    "int", "float", "double", "decimal", "numeric", "real", "bigint", "smallint", "number",
)


def _is_numeric_type(type_str: Optional[str]) -> bool:
    if not type_str:
        return False
    lowered = type_str.lower()
    return any(hint in lowered for hint in _NUMERIC_TYPE_HINTS)


def _normalize_columns(profile: Dict[str, Any]) -> List[Tuple[str, Dict[str, Any]]]:
    """Return (column_name, column_meta) pairs from any known profile shape."""
    if not isinstance(profile, dict):
        return []

    # shared.data_profile.profile_columns() shape: {col: {"dtype": ..., ...}}.
    # Checked first — it carries the richest per-column stats.
    if profile and all(isinstance(v, dict) and "dtype" in v for v in profile.values()):
        return list(profile.items())

    columns_profile = profile.get("columns_profile")
    if isinstance(columns_profile, dict):
        return [(name, meta if isinstance(meta, dict) else {}) for name, meta in columns_profile.items()]

    # Connector-ingestion shape: {"schema": {col: type_str}}.
    schema = profile.get("schema")
    if isinstance(schema, dict):
        return [(name, {"dtype": type_str}) for name, type_str in schema.items()]

    return []


def _classify(meta: Dict[str, Any]) -> Tuple[str, Optional[str], Optional[str]]:
    """Return (field_type, data_type, aggregation) for one column's metadata."""
    data_type = meta.get("dtype") or meta.get("data_type") or meta.get("type")
    if data_type == "numeric" or _is_numeric_type(data_type):
        return "measure", data_type, "sum"
    return "dimension", data_type, None


def _describe(name: str, data_type: Optional[str], meta: Dict[str, Any]) -> str:
    bits = [f"{data_type or 'unknown'} column"]
    if meta.get("distinct") is not None:
        bits.append(f"{meta['distinct']} distinct value(s)")
    null_ratio = meta.get("null_ratio")
    if null_ratio:
        bits.append(f"{null_ratio:.0%} null")
    return f"{name.replace('_', ' ').title()} — {', '.join(bits)}."


class SemanticModelBuilder:
    """Builds a semantic-model payload from a persisted dataset profile."""

    def generate_model_from_profile(
        self, *, file_id: str, dataset_name: str, profile: Dict[str, Any],
    ) -> Dict[str, Any]:
        fields: List[Dict[str, Any]] = []
        for col_name, meta in _normalize_columns(profile):
            field_type, data_type, aggregation = _classify(meta)
            extra_metadata = {k: v for k, v in meta.items() if k not in ("dtype", "data_type", "type")}
            fields.append({
                "id": None,
                "name": col_name,
                "field_type": field_type,
                "data_type": data_type,
                "expression": None,
                "description": _describe(col_name, data_type, meta),
                "aggregation": aggregation,
                "metadata": extra_metadata,
            })

        return {
            "name": dataset_name,
            "description": f"Auto-generated semantic model for {dataset_name} ({len(fields)} column(s)).",
            "source": {"file_id": file_id},
            "tags": ["auto-generated"],
            "fields": fields,
        }


semantic_builder = SemanticModelBuilder()
