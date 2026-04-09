"""
Files Router
=============
File upload, listing, profiling, deletion, and supported-formats endpoints.
"""

import os
import shutil
from typing import Any, Dict

from fastapi import APIRouter, File, HTTPException, UploadFile
from shared.logging_config import get_logger

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


@router.post("/upload")
async def upload_universal(
    file: UploadFile = File(None),
    upload_file: UploadFile = File(None),
):
    """Universal file upload endpoint — accepts both 'file' and 'upload_file' parameter names."""
    target_file = file or upload_file

    if not target_file:
        raise HTTPException(status_code=422, detail="No file sent. Ensure formData uses key 'file'")

    logger.info("Receiving file: %s", target_file.filename)

    try:
        upload_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data", "uploads")
        os.makedirs(upload_dir, exist_ok=True)

        file_path = os.path.join(upload_dir, target_file.filename)
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(target_file.file, buffer)

        logger.info("File saved to %s", file_path)

        return {
            "filename": target_file.filename,
            "status": "success",
            "message": "File uploaded successfully",
        }
    except Exception as e:
        logger.error("Upload failed: %s", e)
        raise HTTPException(status_code=500, detail=f"Server save failed: {str(e)}")


@router.get("/files")
async def list_files() -> Dict[str, Any]:
    """List all uploaded files."""
    if file_service is None:
        return {"status": "error", "error": "File service not available"}
    try:
        files = file_service.list_files()
        return {"status": "success", "files": files}
    except Exception as e:
        return {"status": "error", "error": str(e)}


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
        return {"status": "error", "error": str(e)}


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
        return {"status": "error", "error": str(e)}


@router.delete("/files/{file_id}")
async def delete_file(file_id: str) -> Dict[str, Any]:
    """Delete a file and its processed data."""
    if file_service is None:
        return {"status": "error", "error": "File service not available"}
    try:
        success = file_service.delete_file(file_id)
        if success:
            return {"status": "success", "message": "File deleted successfully"}
        else:
            raise HTTPException(status_code=404, detail="File not found or deletion failed")
    except HTTPException:
        raise
    except Exception as e:
        return {"status": "error", "error": str(e)}
