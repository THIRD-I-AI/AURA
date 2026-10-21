import hashlib
import json
import logging
import mimetypes
import os
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd
from fastapi import HTTPException, UploadFile

logger = logging.getLogger("aura.file_service")


def _match_upload(objects, file_id: str):
    """The one object ``file_id`` names, or None.

    BUG-462: matching "full name OR stem" against every object made a full
    name also hit files whose stem is that name (``sales.csv`` deleted
    ``sales.csv.parquet``), and a bare stem hit every format at once. An exact
    name now wins; a stem resolves only when exactly one object has it.
    """
    objects = list(objects)
    for o in objects:
        if o.name == file_id:
            return o
    by_stem = [o for o in objects if os.path.splitext(o.name)[0] == file_id]
    return by_stem[0] if len(by_stem) == 1 else None


async def purge_upload_metadata(tenant: Optional[str], filename: str) -> None:
    """Drop the metadata-DB rows derived from one upload: its dataset profile
    (which holds sample rows) and its ``schema_columns`` rows (sample values).

    BUG-461: deleting or expiring an upload removed only the files, so these
    rows -- real data from the file -- outlived it, and a semantic model could
    still be built from the deleted file's profile. ``schema_columns`` is keyed
    by stem, so a same-stem sibling's rows go too; they are a rebuildable
    index, while the deleted file's sample values must not survive.
    """
    from metadata_store.repository import get_repository
    from shared.schema_indexer import delete_indexed_source, schema_source_id

    async for repo in get_repository():
        await repo.delete_dataset_profile(filename, workspace_id=tenant)
        break
    await delete_indexed_source(schema_source_id(tenant, filename))


class FileService:
    """Service for handling file uploads, storage, and processing"""

    def __init__(self):
        self.base_path = Path(__file__).parent.parent / "data"
        self.uploads_path = self.base_path / "uploads"
        self.processed_path = self.base_path / "processed"
        self.temp_path = self.base_path / "temp"

        # Ensure directories exist
        self.uploads_path.mkdir(parents=True, exist_ok=True)
        self.processed_path.mkdir(parents=True, exist_ok=True)
        self.temp_path.mkdir(parents=True, exist_ok=True)

        # Supported file types
        self.supported_types = {
            'text/csv': ['.csv'],
            'application/json': ['.json'],
            'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet': ['.xlsx'],
            'application/vnd.ms-excel': ['.xls'],
            'text/plain': ['.txt'],
            'application/octet-stream': ['.parquet'],  # Parquet files
            'application/x-parquet': ['.parquet'],     # Alternative MIME type
        }

        # Maximum file size (25MB - increased for Parquet files)
        self.max_file_size = 25 * 1024 * 1024

    def generate_file_id(self) -> str:
        """Generate unique file ID"""
        return str(uuid.uuid4())

    def calculate_file_hash(self, content: bytes) -> str:
        """Calculate SHA256 hash of file content"""
        return hashlib.sha256(content).hexdigest()

    def validate_file(self, file: UploadFile) -> Dict[str, Any]:
        """Validate uploaded file"""
        file_ext = Path(file.filename).suffix.lower()
        content_type = file.content_type

        supported = False
        for mime_type, extensions in self.supported_types.items():
            if content_type == mime_type or file_ext in extensions:
                supported = True
                break

        if not supported:
            supported_extensions: List[str] = []
            for extensions in self.supported_types.values():
                supported_extensions.extend(extensions)
            raise HTTPException(
                status_code=415,
                detail=f"Unsupported file type. Supported formats: {', '.join(supported_extensions)}"
            )

        return {
            'filename': file.filename,
            'content_type': content_type,
            'file_extension': file_ext,
            'file_size': file.size
        }

    def get_file_info(self, file_id: str, subdir: str = "") -> Optional[Dict[str, Any]]:
        """Metadata for one uploaded object, scoped to ``subdir``'s tenant.

        Was a bare ``pass``, so it returned None for every id and the route
        above it answered 404 for files that plainly existed.

        Matches exactly as ``delete_file`` does (``_match_upload``), so a
        caller may pass either ``sales.csv`` or an unambiguous ``sales``.
        Returns None when this tenant has no such object; the route turns
        that into a 404.

        Tenant-scoped deliberately: resolving the id across all tenants would
        let one org confirm the existence — and read the inferred schema — of
        another org's upload.
        """
        from shared.storage import get_storage_backend
        tenant = subdir or "default"
        obj = _match_upload(get_storage_backend().list(tenant), file_id)
        if obj is not None:
            stem = os.path.splitext(obj.name)[0]
            info: Dict[str, Any] = {
                "file_id": stem,
                "filename": obj.name,
                "size": obj.size,
                "status": "uploaded",
            }
            # The processed sidecar is a local cache: absent in S3 mode and
            # before profiling runs. Enrich when present, never fail when not.
            sidecar = self.processed_path / f"{stem}_processed.json"
            if sidecar.exists():
                try:
                    with open(sidecar, encoding="utf-8") as fh:
                        processed = json.load(fh)
                    info["status"] = "processed"
                    info["preview_data"] = (
                        processed[:5] if isinstance(processed, list) else processed
                    )
                except (OSError, ValueError) as exc:
                    logger.warning("processed sidecar for %s unreadable: %s", stem, exc)
            return info
        return None

    def list_files(self, subdir: str = "") -> List[Dict[str, Any]]:
        """List uploaded files within an optional per-tenant subdir.

        Delegates to the storage backend so the listing is correct in
        both local and S3 modes.  ``subdir`` is passed directly as the
        tenant argument — ``tenant_slug`` inside the backend is
        idempotent, so an already-slugged value is safe.
        """
        from shared.storage import get_storage_backend
        tenant = subdir or "default"
        return [
            {"filename": o.name, "size": o.size, "modified": None}
            for o in get_storage_backend().list(tenant)
        ]

    def delete_file(self, file_id: str, subdir: str = "") -> bool:
        """Delete file (and its processed json) within an optional subdir.

        Deletes the one object ``file_id`` names (see ``_match_upload``).  Processed-data
        artefacts on local disk are still cleaned up as before (they are a
        local cache, not stored in the backend).
        """
        from shared.storage import get_storage_backend
        tenant = subdir or "default"
        backend = get_storage_backend()
        deleted = False
        from shared.data_utils import remove_catalog_files

        o = _match_upload(backend.list(tenant), file_id)
        if o is not None:
            deleted = backend.delete(tenant, o.name)
            remove_catalog_files(o.duckdb_uri)  # BUG-450: sample rows and Parquet copies go with it
        # Clean up local processed-data artefacts (best-effort, harmless if missing).
        try:
            for file_path in self.processed_path.glob(f"{file_id}_processed.*"):
                file_path.unlink()
        except Exception:
            pass
        return deleted

    def _profile_dataframe(self, df: pd.DataFrame) -> Dict[str, Any]:
        """Create lightweight column-level profile for a dataframe."""
        profile: Dict[str, Any] = {
            'rows': int(df.shape[0]),
            'columns': int(df.shape[1]),
            'columns_profile': {}
        }

        for col in df.columns:
            series = df[col]
            col_profile: Dict[str, Any] = {}

            non_null = series.notna().sum()
            nulls = series.isna().sum()
            distinct = series.nunique(dropna=True)

            col_profile['non_null'] = int(non_null)
            col_profile['nulls'] = int(nulls)
            col_profile['distinct'] = int(distinct)

            dtype = self._infer_dtype(series)
            col_profile['inferred_type'] = dtype

            samples = series.dropna().astype(str).unique()[:3].tolist()
            col_profile['samples'] = samples

            if dtype == 'numeric':
                col_profile['min'] = self._to_serializable(series.min())
                col_profile['max'] = self._to_serializable(series.max())
                col_profile['mean'] = self._to_serializable(series.mean())
            elif dtype == 'datetime':
                col_profile['min'] = self._to_serializable(series.min())
                col_profile['max'] = self._to_serializable(series.max())

            value_counts = series.value_counts(dropna=True).head(3)
            col_profile['top_values'] = {str(k): int(v) for k, v in value_counts.items()}

            profile['columns_profile'][str(col)] = col_profile

        return profile

    def _infer_dtype(self, series: pd.Series) -> str:
        """Infer a simple dtype label for profiling purposes."""
        if pd.api.types.is_numeric_dtype(series):
            return 'numeric'
        if pd.api.types.is_datetime64_any_dtype(series):
            return 'datetime'
        return 'categorical'

    def _to_serializable(self, value: Any) -> Any:
        """Convert numpy/pandas scalars to plain Python types for JSON serialization."""
        if isinstance(value, (np.generic,)):
            return value.item()
        if isinstance(value, (pd.Timestamp,)):
            return value.isoformat()
        return value


# Global file service instance
file_service = FileService()
