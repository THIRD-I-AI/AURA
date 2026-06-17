"""
Files Router
=============
File upload, listing, profiling, deletion, and supported-formats endpoints.
"""

import os
import uuid
from typing import Any, Dict, Optional

from fastapi import APIRouter, File, Header, HTTPException, UploadFile

from shared.data_utils import invalidate_schema_cache
from shared.error_handler import sanitize_error
from shared.logging_config import get_logger
from shared.streaming_manager import TOPIC_UPLOAD, streaming_manager

logger = get_logger("aura.api_gateway.files")

router = APIRouter(tags=["Files"])


# ── File service ─────────────────────────────────────────────────────

try:
    from shared.file_service import file_service
except ImportError as e:
    logger.warning("File service not available: %s", e)
    file_service = None

try:
    from metadata_store.repository import get_repository
except ImportError:
    get_repository = None


# ── Endpoints ────────────────────────────────────────────────────────

@router.get("/files/supported-formats")
def get_supported_formats() -> Dict[str, Any]:
    """Get list of supported file formats."""
    return {
        "status": "success",
        "supported_formats": {
            "csv": {"extensions": [".csv"], "description": "Comma-separated values", "icon": "📊"},
            "excel": {"extensions": [".xlsx", ".xls"], "description": "Microsoft Excel", "icon": "📈"},
            "json": {"extensions": [".json"], "description": "JavaScript Object Notation", "icon": "🔗"},
            "text": {"extensions": [".txt"], "description": "Plain text files", "icon": "📄"},
            "parquet": {"extensions": [".parquet"], "description": "Apache Parquet columnar storage", "icon": "🗃️"},
        },
        "max_file_size": "25MB",
        "notes": {
            "parquet": "Optimized for analytics workloads, supports compression and efficient querying",
            "csv": "Most common format, human-readable",
            "excel": "Supports multiple sheets and formatting",
            "json": "Flexible structure, good for nested data",
        },
    }


def _safe_upload_path(upload_dir: str, filename: Optional[str]) -> Optional[str]:
    """Resolve a client-supplied filename to a path INSIDE ``upload_dir``.

    The client controls the filename, so a value like
    ``"../keys/signing_ed25519.pem"`` must not escape the upload dir and
    overwrite arbitrary files. We strip to the basename and then assert the
    resolved path stays contained (belt-and-suspenders). Returns the safe
    absolute-ish path, or ``None`` for a degenerate name ("", ".", "..").
    """
    safe_name = os.path.basename((filename or "").replace("\\", "/")).strip()
    # Reject degenerate names and a NUL byte (which would otherwise pass the
    # containment check but blow up at open() with a messy 500 instead of 400).
    if not safe_name or safe_name in (".", "..") or "\x00" in safe_name:
        return None
    file_path = os.path.join(upload_dir, safe_name)
    if os.path.commonpath((os.path.abspath(file_path), os.path.abspath(upload_dir))) != os.path.abspath(upload_dir):
        return None
    return file_path


@router.post("/upload")
async def upload_universal(
    file: UploadFile = File(None),
    upload_file: UploadFile = File(None),
    x_upload_id: Optional[str] = Header(None, alias="X-Upload-Id"),
):
    """Universal file upload endpoint — accepts both 'file' and 'upload_file' parameter names.

    Publishes real-time progress to SSE topic ``upload:{upload_id}``. The client
    may provide its own id via the ``X-Upload-Id`` header (recommended so it can
    subscribe before POSTing); otherwise one is generated server-side.
    """
    target_file = file or upload_file

    if not target_file:
        # 400 not 422 — Pydantic's ValidationError schema (detail: array)
        # owns 422 in FastAPI's OpenAPI emission. Using 422 with a plain
        # string detail causes Schemathesis to flag a schema/implementation
        # mismatch. This is a "bad request payload", not a Pydantic
        # validation failure, so 400 is the correct code.
        raise HTTPException(status_code=400, detail="No file sent. Ensure formData uses key 'file'")

    upload_id = x_upload_id or uuid.uuid4().hex
    logger.info("Receiving file: %s (upload_id=%s)", target_file.filename, upload_id)

    # Path-traversal guard — the client controls the filename (see
    # _safe_upload_path). Without this a name like "../keys/signing_ed25519.pem"
    # could escape the upload dir and overwrite arbitrary files.
    upload_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data", "uploads")
    os.makedirs(upload_dir, exist_ok=True)
    file_path = _safe_upload_path(upload_dir, target_file.filename)
    if file_path is None:
        raise HTTPException(status_code=400, detail="Invalid filename")
    safe_name = os.path.basename(file_path)

    try:
        await streaming_manager.publish_progress(
            TOPIC_UPLOAD, upload_id,
            f"Receiving {target_file.filename}", 0.05,
            extra={"stage": "receiving", "filename": target_file.filename},
        )

        bytes_written = 0
        with open(file_path, "wb") as buffer:
            while True:
                chunk = await target_file.read(1024 * 256)  # 256 KB
                if not chunk:
                    break
                buffer.write(chunk)
                bytes_written += len(chunk)
                total = getattr(target_file, "size", None) or 0
                if total > 0:
                    pct = min(0.85, 0.10 + 0.75 * (bytes_written / total))
                    await streaming_manager.publish_progress(
                        TOPIC_UPLOAD, upload_id,
                        f"Streaming {bytes_written // 1024} KB",
                        pct,
                        extra={"stage": "streaming", "bytes": bytes_written, "total": total},
                    )

        await streaming_manager.publish_progress(
            TOPIC_UPLOAD, upload_id,
            "Saved to disk", 0.90,
            extra={"stage": "saved", "path": file_path, "bytes": bytes_written},
        )

        logger.info("File saved to %s (%d bytes)", file_path, bytes_written)

        result = {
            "upload_id": upload_id,
            "filename": safe_name,
            "bytes": bytes_written,
            "status": "success",
            "message": "File uploaded successfully",
        }

        await invalidate_schema_cache()

        # Structured schema indexer — populates `schema_columns` so the
        # MCP server's metadata.search_columns / describe_table tools can
        # answer with exact (source, table, column, dtype) rows instead
        # of LIKE-grepping a free-text body. Backgrounded and best-effort:
        # never delays the upload response, never fails it.
        try:
            from shared.schema_indexer import index_uploaded_file
            from shared.tasks import fire_and_forget
            fire_and_forget(index_uploaded_file(file_path), name=f"schema-index-{upload_id}")
        except Exception as _idx_exc:
            logger.warning("schema_indexer dispatch failed (non-fatal): %s", _idx_exc)

        # Sprint P-2a: file metadata cache — populates
        # gateway_file_metadata so the dashboard-stats endpoint reads
        # cached row counts instead of running COUNT(*) per file per
        # request. Backgrounded + best-effort like the schema indexer
        # above. The 60s background tick is the defensive fallback if
        # this dispatch is dropped for any reason.
        try:
            from api_gateway.persistence import index_file_metadata
            from shared.tasks import fire_and_forget
            fire_and_forget(index_file_metadata(file_path), name=f"file-meta-{upload_id}")
        except Exception as _meta_exc:
            logger.warning(
                "file_metadata cache dispatch failed (non-fatal): %s", _meta_exc,
            )

        # Sprint P-2b: schema context cache — rebuilds gateway_schema_context
        # with full LLM enrichment so future query executions read the
        # cached context instead of running LLM inference inline. Same
        # best-effort, non-blocking pattern as the file-metadata hook above.
        try:
            from api_gateway.persistence import refresh_schema_context
            from shared.tasks import fire_and_forget
            fire_and_forget(refresh_schema_context([upload_dir]), name=f"schema-ctx-{upload_id}")
        except Exception as _ctx_exc:
            logger.warning(
                "schema_context rebuild dispatch failed (non-fatal): %s", _ctx_exc,
            )

        await streaming_manager.publish_complete(TOPIC_UPLOAD, upload_id, result)
        return result
    except Exception as e:
        safe_message = sanitize_error(e, logger=logger, context=f"upload {upload_id}")
        await streaming_manager.publish_error(
            TOPIC_UPLOAD, upload_id, safe_message, code="UPLOAD_FAILED",
        )
        raise HTTPException(status_code=500, detail=safe_message)


@router.get("/files")
async def list_files() -> Dict[str, Any]:
    """List all uploaded files."""
    if file_service is None:
        return {"status": "error", "error": "File service not available"}
    try:
        files = file_service.list_files()
        return {"status": "success", "files": files}
    except Exception as e:
        return {"status": "error", "error": sanitize_error(e, logger=logger, context="list files")}


@router.get("/files/{file_id}")
async def get_file_info(file_id: str) -> Dict[str, Any]:
    """Get information about a specific file."""
    if file_service is None:
        return {"status": "error", "error": "File service not available"}
    try:
        file_info = file_service.get_file_info(file_id)
        if file_info:
            return {"status": "success", "file_info": file_info}
        else:
            raise HTTPException(status_code=404, detail="File not found")
    except HTTPException:
        raise
    except Exception as e:
        return {"status": "error", "error": sanitize_error(e, logger=logger, context="get file info")}


@router.get("/files/{file_id}/profile")
async def get_file_profile(file_id: str) -> Dict[str, Any]:
    """Fetch stored profile for a file if available."""
    if get_repository is None:
        return {"status": "error", "error": "Metadata repository not available"}
    try:
        async for repo in get_repository():
            profile = await repo.get_dataset_profile(file_id)
            break
        if profile is None:
            raise HTTPException(status_code=404, detail="Profile not found")
        return {
            "status": "success",
            "file_id": file_id,
            "dataset_name": profile.dataset_name,
            "rows_count": profile.rows_count,
            "columns_count": profile.columns_count,
            "profile": profile.profile,
            "updated_at": profile.updated_at,
        }
    except HTTPException:
        raise
    except Exception as e:
        return {"status": "error", "error": sanitize_error(e, logger=logger, context="get file profile")}


@router.delete("/files/{file_id}")
async def delete_file(file_id: str) -> Dict[str, Any]:
    """Delete a file and its processed data."""
    if file_service is None:
        return {"status": "error", "error": "File service not available"}
    try:
        success = file_service.delete_file(file_id)
        if success:
            await invalidate_schema_cache()
            return {"status": "success", "message": "File deleted successfully"}
        else:
            raise HTTPException(status_code=404, detail="File not found or deletion failed")
    except HTTPException:
        raise
    except Exception as e:
        return {"status": "error", "error": sanitize_error(e, logger=logger, context="delete file")}
