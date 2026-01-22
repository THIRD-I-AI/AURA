# AURA Orchestrator Guide

## Overview
The `orchestrator.py` script starts all backend microservices in parallel with proper PYTHONPATH configuration and unified logging.

## Corrected Service Paths

The orchestrator now uses the actual service locations in your project:

| Service | Port | Module Path |
|---------|------|------------|
| API Gateway | 8000 | `aurabackend.api_gateway.enhanced_main:app` |
| Code Generation | 8001 | `aurabackend.code_generation_service.main:code_gen_app` |
| Execution Service | 8003 | `aurabackend.execution_sandbox.main:execution_app` |
| Scheduler Service | 8004 | `aurabackend.scheduler_service.main:scheduler_app` |
| Orchestration Service | 8006 | `aurabackend.orchestration_service.main:orchestration_app` |

## How It Works

1. **Module Format**: Services are specified as `module.path:app_object`
   - Example: `aurabackend.api_gateway.enhanced_main:app`
   - This means: Import the `app` FastAPI object from `aurabackend/api_gateway/enhanced_main.py`

2. **PYTHONPATH Configuration**: 
   - The orchestrator automatically sets `PYTHONPATH` to the project root
   - This enables proper imports of `aurabackend.*` modules

3. **Uvicorn Execution**:
   - Each service is started with: `python -m uvicorn module:app --host 0.0.0.0 --port PORT`
   - This allows FastAPI apps to run without needing their own `if __name__ == "__main__"` blocks

## Usage

### Start All Services
```bash
cd c:\Users\mouni\Documents\GitHub\Data-Analyst-Agent\Data-Analyst-Agent
python orchestrator.py
```

**Output:**
```
================================================================================
                      AURA Backend Orchestrator
================================================================================

[HH:MM:SS] [Orchestrator        ] Project root: C:\Users\...\Data-Analyst-Agent
[HH:MM:SS] [Orchestrator        ] Starting 5 services...

[HH:MM:SS] [API Gateway         ] Starting on port 8000...
[HH:MM:SS] [API Gateway         ] Started (PID: xxxxx)

[HH:MM:SS] [Code Generation Service] Starting on port 8001...
[HH:MM:SS] [Code Generation Service] Started (PID: xxxxx)

...

[HH:MM:SS] [Orchestrator        ] Started 5/5 services

✓ All services started successfully!

Backend available at: http://localhost:8000
Frontend should connect via: /api (Vite proxy)
```

### Stop Services
Press **Ctrl+C** to gracefully shutdown all services.

The orchestrator will:
1. Send SIGTERM to all processes (graceful shutdown)
2. Wait 2 seconds for graceful termination
3. Send SIGKILL to any remaining processes
4. Report final status

## Service Details

### API Gateway (Port 8000)
- **File**: `aurabackend/api_gateway/enhanced_main.py`
- **App**: `app` (FastAPI instance)
- **Entry Point**: Has `if __name__ == "__main__": uvicorn.run(app, ...)`
- **Note**: Also works via `python -m uvicorn aurabackend.api_gateway.enhanced_main:app`

### Code Generation Service (Port 8001)
- **File**: `aurabackend/code_generation_service/main.py`
- **App**: `code_gen_app` (FastAPI instance)
- **Entry Point**: No `if __name__` block - must use uvicorn
- **Endpoints**: `/health`, `/generate_code`

### Execution Service (Port 8003)
- **File**: `aurabackend/execution_sandbox/main.py`
- **App**: `execution_app` (FastAPI instance)
- **Entry Point**: No `if __name__` block - must use uvicorn
- **Endpoints**: `/health`, `/execute_sql`

### Scheduler Service (Port 8004)
- **File**: `aurabackend/scheduler_service/main.py`
- **App**: `scheduler_app` (FastAPI instance)
- **Entry Point**: No `if __name__` block - must use uvicorn
- **Endpoints**: `/health`, `/jobs/*`

### Orchestration Service (Port 8006)
- **File**: `aurabackend/orchestration_service/main.py`
- **App**: `orchestration_app` (FastAPI instance)
- **Entry Point**: No `if __name__` block - must use uvicorn

## Frontend Integration

The frontend is configured to proxy API calls via Vite:

**vite.config.ts:**
```typescript
server: {
  proxy: {
    '/api': {
      target: 'http://localhost:8000',
      changeOrigin: true,
      rewrite: (path) => path.replace(/^\/api/, ''),
    },
    '/ws': {
      target: 'ws://localhost:8000',
      ws: true,
    },
  },
}
```

**frontend/src/services/api.ts:**
```typescript
const API_BASE_URL = '/api';  // Relative path for proxy
```

## Troubleshooting

### "Module not found"
- **Cause**: Missing `aurabackend` package
- **Fix**: Ensure `aurabackend/__init__.py` exists and PYTHONPATH is set correctly

### Port Already in Use
- **Cause**: Services from previous run still listening
- **Fix**: `netstat -ano | findstr :8000` to find PID, then kill it
- **Or**: Change port in SERVICES list

### Services Exit Immediately
- **Cause**: Missing dependencies or import errors
- **Fix**: Check service logs and run `pip install -r aurabackend/requirements.txt`

### Uvicorn Not Found
- **Cause**: uvicorn not installed
- **Fix**: `pip install uvicorn[standard]`

## Commands for Testing

### Check Health Endpoints
```bash
Invoke-WebRequest -Uri "http://localhost:8000/health" -UseBasicParsing
Invoke-WebRequest -Uri "http://localhost:8001/health" -UseBasicParsing
Invoke-WebRequest -Uri "http://localhost:8003/health" -UseBasicParsing
Invoke-WebRequest -Uri "http://localhost:8004/health" -UseBasicParsing
Invoke-WebRequest -Uri "http://localhost:8006/health" -UseBasicParsing
```

### List Running Services
```bash
netstat -ano | Select-String ":800"
Get-Process python | Select-Object ProcessName, Id
```

### Kill All Python Processes
```bash
Get-Process python | Stop-Process -Force
```

## Architecture

```
┌─────────────────────────────────────────────────────┐
│  Frontend (localhost:5173)                          │
│  - Vite Dev Server                                  │
│  - Routes /api → localhost:8000                     │
│  - Routes /ws → ws://localhost:8000                 │
└──────────────────┬──────────────────────────────────┘
                   │ /api proxy
┌──────────────────▼──────────────────────────────────┐
│  API Gateway (8000) - Router & Load Balancer        │
│  - Validates queries                                 │
│  - Routes to appropriate service                     │
│  - Handles CORS                                      │
└──────────────────┬──────────────────────────────────┘
                   │
        ┌──────────┼──────────┬──────────┐
        │          │          │          │
┌───────▼────┬────▼──────┬───▼──────┬───▼─────┐
│Code Gen    │Execution  │Scheduler │Orchestr │
│(8001)      │(8003)     │(8004)    │(8006)   │
└────────────┴───────────┴──────────┴─────────┘
```

## Notes

- **Unified Logging**: All services log to console with timestamps and service names
- **Graceful Shutdown**: Ctrl+C sends signals to all processes properly
- **PYTHONPATH Management**: Automatically configured by orchestrator
- **Port Availability**: Orchestrator waits 5 seconds per service for startup verification
- **Process Monitoring**: Dead processes are detected and reported
