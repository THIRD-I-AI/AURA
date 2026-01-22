# Service Entry Points - Verified

This document confirms the actual service entry points found in the project.

## Scan Results

### 1. API Gateway ✅
**Location**: `aurabackend/api_gateway/enhanced_main.py`
```python
# Has FastAPI app named 'app'
app = FastAPI(
    title="AURA API Gateway",
    description="Enterprise data analytics platform gateway",
    version="2.0.0"
)

# Has main entry point
if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
```
**Orchest rtor**: `aurabackend.api_gateway.enhanced_main:app`
**Port**: 8000
**Status**: ✅ VERIFIED

### 2. Code Generation Service ✅
**Location**: `aurabackend/code_generation_service/main.py`
```python
# Has FastAPI app named 'code_gen_app'
code_gen_app = FastAPI(title="AURA Code Generation Service")

@code_gen_app.get("/health")
async def health() -> Dict[str, str]:
    return {"status": "healthy", "service": "code_generation"}

@code_gen_app.post("/generate_code")
async def generate_code(step: PlanStep) -> Dict[str, Any]:
    ...
```
**Orchestrator**: `aurabackend.code_generation_service.main:code_gen_app`
**Port**: 8001
**Status**: ✅ VERIFIED
**Note**: No `if __name__` block - must use uvicorn

### 3. Execution Service ✅
**Location**: `aurabackend/execution_sandbox/main.py`
```python
# Has FastAPI app named 'execution_app'
execution_app = FastAPI(title="AURA Execution Sandbox")

@execution_app.get("/health")
async def health():
    return {"status": "healthy", "service": "execution_sandbox"}

@execution_app.post("/execute_sql", response_model=QueryResult)
async def execute_sql(job: ExecutionJob) -> QueryResult:
    ...
```
**Orchestrator**: `aurabackend.execution_sandbox.main:execution_app`
**Port**: 8003
**Status**: ✅ VERIFIED
**Note**: No `if __name__` block - must use uvicorn

### 4. Scheduler Service ✅
**Location**: `aurabackend/scheduler_service/main.py`
```python
# Has FastAPI app named 'scheduler_app'
scheduler_app = FastAPI(title="AURA Scheduler Service")

@scheduler_app.get("/health")
async def health():
    return {"status": "healthy", "service": "scheduler"}

# Has comprehensive job management endpoints
@scheduler_app.post("/jobs")
async def create_job(request: CreateJobRequest) -> JobResponse:
    ...
```
**Orchestrator**: `aurabackend.scheduler_service.main:scheduler_app`
**Port**: 8004
**Status**: ✅ VERIFIED
**Note**: No `if __name__` block - must use uvicorn

### 5. Orchestration Service ✅
**Location**: `aurabackend/orchestration_service/main.py`
```python
# Has FastAPI app named 'orchestration_app'
orchestration_app = FastAPI(title="AURA Orchestration Service")

@orchestration_app.get("/health")
async def health():
    return {"status": "healthy", "service": "orchestration"}

# Coordinates between services
@orchestration_app.post("/coordinate")
async def coordinate(...):
    ...
```
**Orchestrator**: `aurabackend.orchestration_service.main:orchestration_app`
**Port**: 8006
**Status**: ✅ VERIFIED
**Note**: No `if __name__` block - must use uvicorn

## Services NOT Found (Removed from Orchestrator)

### ❌ Connector Service
**Original Path** (incorrect): `aurabackend/connectors/service.py`
**Actual Structure**: 
- `aurabackend/connectors/` has: `base.py`, `bigquery_connector.py`, `mysql_connector.py`, `postgresql_connector.py`
- No `service.py` entry point file
- Connectors are used as classes by API Gateway, not standalone services

### ❌ Insights Service
**Original Path** (incorrect): `aurabackend/insights/service.py`
**Actual Structure**:
- `aurabackend/insights/` has: `engine.py`, `__init__.py`
- No standalone service (used by API Gateway internally)

## Launch Command Pattern

All services now launch with this pattern:
```bash
python -m uvicorn aurabackend.{service}.{module}:{app_name} --host 0.0.0.0 --port {PORT}
```

Examples:
```bash
python -m uvicorn aurabackend.api_gateway.enhanced_main:app --host 0.0.0.0 --port 8000
python -m uvicorn aurabackend.code_generation_service.main:code_gen_app --host 0.0.0.0 --port 8001
python -m uvicorn aurabackend.execution_sandbox.main:execution_app --host 0.0.0.0 --port 8003
python -m uvicorn aurabackend.scheduler_service.main:scheduler_app --host 0.0.0.0 --port 8004
python -m uvicorn aurabackend.orchestration_service.main:orchestration_app --host 0.0.0.0 --port 8006
```

## Dependencies

All services import from shared modules:
- `from shared.models import ...`
- `from shared.secret_resolver import ...`
- `from shared.file_service import ...`
- `from connectors import ...` (API Gateway only)
- `from safety import ...` (API Gateway only)
- `from insights import ...` (API Gateway only)
- `from metadata_store import ...` (API Gateway only)

The orchestrator sets `PYTHONPATH` to project root to enable these imports.

## Verification Commands

### Check all services have health endpoints:
```bash
$ports = @(8000, 8001, 8003, 8004, 8006)
foreach ($port in $ports) {
    try {
        $response = Invoke-WebRequest -Uri "http://localhost:$port/health" -UseBasicParsing
        Write-Host "Port $port - OK: $($response.Content)"
    } catch {
        Write-Host "Port $port - FAILED"
    }
}
```

### Check if orchestrator is running:
```bash
Get-Process python | Select-Object ProcessName, Id, CommandLine | Select-String uvicorn
```

Should show one Python process per service using uvicorn.

### Kill all services:
```bash
Get-Process python | Stop-Process -Force
```

## Summary

✅ 5/5 services mapped to correct locations
❌ 2 removed (not actual services)
✅ PYTHONPATH configuration added
✅ Uvicorn launch method implemented
✅ All services use proper FastAPI app objects
