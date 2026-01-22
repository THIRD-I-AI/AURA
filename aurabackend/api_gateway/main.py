
from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import httpx
import sys
import os
from pathlib import Path
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from dotenv import load_dotenv

# Load environment variables
load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))

# Add the parent directory to Python path for imports
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Import file service
try:
    from shared.file_service import file_service
except ImportError as e:
    print(f"Warning: File service not available - {e}")
    file_service = None

try:
    from metadata_store.repository import get_repository
except ImportError as e:
    print(f"Warning: Metadata repository not available - {e}")
    get_repository = None

try:
    from semantic_builder import semantic_builder
except ImportError as e:
    print(f"Warning: Semantic builder not available - {e}")
    semantic_builder = None

FILE_SERVICE_UNAVAILABLE = "File service not available"

# Define models directly here to avoid import issues
class ChatRequest(BaseModel):
    message: str
    context: Optional[str] = None

class QueryRequest(BaseModel):
    session_id: str
    prompt: str
    context: Optional[str] = None

class AgentResponse(BaseModel):
    response: str
    confidence: float
    suggestions: List[str] = []
    metadata: Dict[str, Any] = {}


class SemanticFieldPayload(BaseModel):
    id: Optional[str] = None
    name: str
    field_type: str = Field(default="dimension", description="dimension | measure")
    data_type: Optional[str] = None
    expression: Optional[str] = None
    description: Optional[str] = None
    aggregation: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class SemanticModelPayload(BaseModel):
    id: Optional[str] = None
    name: str
    description: Optional[str] = None
    source: Dict[str, Any] = Field(default_factory=dict)
    tags: List[str] = Field(default_factory=list)
    fields: List[SemanticFieldPayload] = Field(default_factory=list)

api_gateway = FastAPI()

# Add CORS middleware to allow frontend connections
allowed_origins = os.getenv(
    "ALLOWED_ORIGINS",
    "http://localhost:5173,http://localhost:5174,http://localhost:3000,http://127.0.0.1:5173,http://127.0.0.1:5174,http://127.0.0.1:3000"
).split(",")
api_gateway.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@api_gateway.get("/")
def root():
	return {"message": "API Gateway is running."}

@api_gateway.get("/health")
def health_check():
    return {"status": "healthy", "service": "api_gateway"}

@api_gateway.get("/files/supported-formats")
def get_supported_formats() -> Dict[str, Any]:
    """Get list of supported file formats"""
    try:
        return {
            "status": "success",
            "supported_formats": {
                "csv": {"extensions": [".csv"], "description": "Comma-separated values", "icon": "📊"},
                "excel": {"extensions": [".xlsx", ".xls"], "description": "Microsoft Excel", "icon": "📈"},
                "json": {"extensions": [".json"], "description": "JavaScript Object Notation", "icon": "🔗"},
                "text": {"extensions": [".txt"], "description": "Plain text files", "icon": "📄"},
                "parquet": {"extensions": [".parquet"], "description": "Apache Parquet columnar storage", "icon": "🗃️"}
            },
            "max_file_size": "25MB",
            "notes": {
                "parquet": "Optimized for analytics workloads, supports compression and efficient querying",
                "csv": "Most common format, human-readable",
                "excel": "Supports multiple sheets and formatting",
                "json": "Flexible structure, good for nested data"
            }
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}

@api_gateway.post("/chat")
async def chat_endpoint(request: ChatRequest) -> Dict[str, Any]:
    """Main chat endpoint for AI interactions"""
    try:
        # For now, return a simple response
        # Later this will integrate with AI services
        return {
            "response": f"Received your message: {request.message}",
            "confidence": 0.95,
            "suggestions": ["Try asking about data analysis", "Connect to a database", "Create visualizations"],
            "metadata": {"timestamp": "now", "service": "api_gateway"}
        }
    except Exception as e:
        return {"error": str(e), "status": "error"}

@api_gateway.post("/generate_query")
async def generate_query_proxy(request: QueryRequest) -> Dict[str, Any]:
    """Proxy query generation to orchestration service."""
    target_url = os.getenv(
        "ORCHESTRATION_SERVICE_URL",
        "http://localhost:8001/v1/orchestrations/query",
    )
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.post(target_url, json=request.model_dump())
            response.raise_for_status()
            payload = response.json()
            return payload
    except httpx.HTTPStatusError as http_exc:
        return {
            "status": "Error",
            "error_message": f"Orchestration error: {http_exc.response.text}",
            "final_query": "-- Error generating query",
        }
    except Exception as exc:
        error_msg = str(exc)
        print(f"[ERROR] generate_query_proxy: {error_msg}")
        return {
            "status": "Error",
            "error_message": f"Backend error: {error_msg}",
            "final_query": "-- Error generating query",
            "details": error_msg
        }

@api_gateway.get("/databases/test/{db_type}")
async def test_database_connection(db_type: str):
    """Proxy to database service for connection testing"""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(f"http://localhost:8002/databases/test/{db_type}")
            return response.json()
    except Exception as e:
        return {"error": f"Database service unavailable: {str(e)}", "status": "error"}

# File Upload Endpoints
@api_gateway.post("/files/upload")
async def upload_file(file: UploadFile = File(...)) -> Dict[str, Any]:
    """Upload and process a data file (CSV, JSON, Excel, TXT, Parquet)"""
    if file_service is None:
        raise HTTPException(status_code=503, detail=FILE_SERVICE_UNAVAILABLE)
    
    try:
        if not hasattr(file, "size") or file.size is None:
            try:
                file.file.seek(0, os.SEEK_END)
                file_size = file.file.tell()
                file.file.seek(0)
                setattr(file, "size", file_size)
            except Exception:
                setattr(file, "size", None)

        # Save file
        file_metadata = await file_service.save_file(file)
        
        # Process file
        processed_metadata = await file_service.process_file(file_metadata)

        # Persist dataset profile if repository is available
        if get_repository is not None:
            try:
                async for repo in get_repository():
                    await repo.upsert_dataset_profile(
                        file_id=processed_metadata["file_id"],
                        dataset_name=Path(processed_metadata["original_filename"]).stem,
                        profile=processed_metadata.get("profile", {}),
                        rows_count=processed_metadata.get("rows_count"),
                        columns_count=processed_metadata.get("columns_count"),
                    )
                    break
            except Exception as repo_exc:
                print(f"[WARN] Failed to persist dataset profile: {repo_exc}")
        
        return {
            "status": "success",
            "message": "File uploaded and processed successfully",
            "file_info": {
                "file_id": processed_metadata["file_id"],
                "original_filename": processed_metadata["original_filename"],
                "file_size": processed_metadata["file_size"],
                "rows_count": processed_metadata["rows_count"],
                "columns_count": processed_metadata["columns_count"],
                "upload_time": processed_metadata["upload_time"],
                "processed_time": processed_metadata["processed_time"]
            },
            "preview": processed_metadata.get("preview_data", []),
            "profile": processed_metadata.get("profile", {})
        }
    except HTTPException as e:
        raise e
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Upload failed: {str(e)}")

@api_gateway.get("/files")
async def list_files() -> Dict[str, Any]:
    """List all uploaded files"""
    if file_service is None:
        return {"status": "error", "error": FILE_SERVICE_UNAVAILABLE}
    
    try:
        files = file_service.list_files()
        return {"status": "success", "files": files}
    except Exception as e:
        return {"status": "error", "error": str(e)}

@api_gateway.get("/files/{file_id}")
async def get_file_info(file_id: str) -> Dict[str, Any]:
    """Get information about a specific file"""
    if file_service is None:
        return {"status": "error", "error": FILE_SERVICE_UNAVAILABLE}
    
    try:
        file_info = file_service.get_file_info(file_id)
        if file_info:
            return {"status": "success", "file_info": file_info}
        else:
            raise HTTPException(status_code=404, detail="File not found")
    except HTTPException as e:
        raise e
    except Exception as e:
        return {"status": "error", "error": str(e)}


@api_gateway.get("/files/{file_id}/profile")
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

@api_gateway.delete("/files/{file_id}")
async def delete_file(file_id: str) -> Dict[str, Any]:
    """Delete a file and its processed data"""
    if file_service is None:
        return {"status": "error", "error": FILE_SERVICE_UNAVAILABLE}
    
    try:
        success = file_service.delete_file(file_id)
        if success:
            return {"status": "success", "message": "File deleted successfully"}
        else:
            raise HTTPException(status_code=404, detail="File not found or deletion failed")
    except HTTPException as e:
        raise e
    except Exception as e:
        return {"status": "error", "error": str(e)}


def _serialize_semantic_model(model: Any) -> Dict[str, Any]:
    return {
        "id": model.id,
        "name": model.name,
        "description": model.description,
        "source": model.source,
        "tags": model.tags,
        "created_at": model.created_at,
        "updated_at": model.updated_at,
        "fields": [
            {
                "id": field.id,
                "name": field.name,
                "field_type": field.field_type,
                "data_type": field.data_type,
                "expression": field.expression,
                "description": field.description,
                "aggregation": field.aggregation,
                "metadata": field.metadata,
                "created_at": field.created_at,
                "updated_at": field.updated_at,
            }
            for field in getattr(model, "fields", [])
        ],
    }


@api_gateway.post("/semantic/models/from-file/{file_id}")
async def auto_generate_model_from_file(file_id: str) -> Dict[str, Any]:
    """Auto-generate semantic model from dataset profile (no hardcoding)."""
    if semantic_builder is None or get_repository is None:
        return {"status": "error", "error": "Semantic builder or repository not available"}

    try:
        async for repo in get_repository():
            # Fetch the stored profile
            profile_record = await repo.get_dataset_profile(file_id)
            if profile_record is None:
                raise HTTPException(status_code=404, detail="Dataset profile not found")

            # Auto-generate model payload from profile
            model_payload = semantic_builder.generate_model_from_profile(
                file_id=file_id,
                dataset_name=profile_record.dataset_name or f"dataset_{file_id[:8]}",
                profile=profile_record.profile,
            )

            # Persist the generated model
            model = await repo.upsert_semantic_model(
                model_id=None,
                name=model_payload['name'],
                description=model_payload['description'],
                source=model_payload['source'],
                tags=model_payload['tags'],
                fields=model_payload['fields'],
            )

            break

        return {"status": "success", "model": _serialize_semantic_model(model)}
    except HTTPException:
        raise
    except Exception as e:
        return {"status": "error", "error": str(e)}


@api_gateway.post("/semantic/models")
async def upsert_semantic_model(payload: SemanticModelPayload) -> Dict[str, Any]:
    if get_repository is None:
        return {"status": "error", "error": "Metadata repository not available"}

    try:
        async for repo in get_repository():
            model = await repo.upsert_semantic_model(
                model_id=payload.id,
                name=payload.name,
                description=payload.description,
                source=payload.source,
                tags=payload.tags,
                fields=[field.model_dump() for field in payload.fields],
            )
            break
        return {"status": "success", "model": _serialize_semantic_model(model)}
    except Exception as e:
        return {"status": "error", "error": str(e)}


@api_gateway.get("/semantic/models")
async def list_semantic_models() -> Dict[str, Any]:
    if get_repository is None:
        return {"status": "error", "error": "Metadata repository not available"}

    try:
        async for repo in get_repository():
            models = await repo.list_semantic_models()
            break
        return {"status": "success", "models": [_serialize_semantic_model(model) for model in models]}
    except Exception as e:
        return {"status": "error", "error": str(e)}


@api_gateway.get("/semantic/models/{model_id}")
async def get_semantic_model(model_id: str) -> Dict[str, Any]:
    if get_repository is None:
        return {"status": "error", "error": "Metadata repository not available"}

    try:
        async for repo in get_repository():
            model = await repo.get_semantic_model(model_id)
            break
        if model is None:
            raise HTTPException(status_code=404, detail="Semantic model not found")
        return {"status": "success", "model": _serialize_semantic_model(model)}
    except HTTPException:
        raise
    except Exception as e:
        return {"status": "error", "error": str(e)}

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("API_GATEWAY_PORT", 8000))
    host = os.getenv("API_HOST", "0.0.0.0")
    debug = os.getenv("DEBUG", "false").lower() == "true"
    
    print(f"🌐 Starting AURA API Gateway on {host}:{port}...")
    if debug:
        print("🐛 Debug mode enabled")
    
    uvicorn.run("main:api_gateway", host=host, port=port, reload=debug)
