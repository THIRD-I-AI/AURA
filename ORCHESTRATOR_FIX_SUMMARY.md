# Orchestrator Fix Summary

## Problem
The original `orchestrator.py` had incorrect service paths that didn't match the actual project structure:
- Paths like `aurabackend/connectors/service.py` didn't exist (there's no entry point there)
- Missing `aurabackend/insights/service.py`
- Missing `aurabackend/orchestration_service/scheduler.py`
- No PYTHONPATH configuration for proper imports
- Not using uvicorn to start FastAPI apps

## Solution
Scanned the actual project structure and fixed the orchestrator:

### Corrected Service Paths
```python
SERVICES = [
    {
        "name": "API Gateway",
        "port": 8000,
        "module": "aurabackend.api_gateway.enhanced_main:app",
    },
    {
        "name": "Code Generation Service",
        "port": 8001,
        "module": "aurabackend.code_generation_service.main:code_gen_app",
    },
    {
        "name": "Execution Service",
        "port": 8003,
        "module": "aurabackend.execution_sandbox.main:execution_app",
    },
    {
        "name": "Scheduler Service",
        "port": 8004,
        "module": "aurabackend.scheduler_service.main:scheduler_app",
    },
    {
        "name": "Orchestration Service",
        "port": 8006,
        "module": "aurabackend.orchestration_service.main:orchestration_app",
    },
]
```

### Key Changes

#### 1. Module Format with Uvicorn
**Before:**
```python
# Tried to directly run Python scripts that didn't have __main__ blocks
module = "aurabackend/connectors/service.py"  # Doesn't exist!
process = subprocess.Popen([sys.executable, str(module_path)])
```

**After:**
```python
# Use module:app format with uvicorn
module = "aurabackend.code_generation_service.main:code_gen_app"
command = [
    sys.executable,
    "-m",
    "uvicorn",
    module,
    "--host", "0.0.0.0",
    "--port", str(port),
]
```

#### 2. PYTHONPATH Configuration
**Before:**
```python
class ServiceOrchestrator:
    def __init__(self):
        self.processes = {}
        self.project_root = Path(__file__).parent
        # No PYTHONPATH setup!
```

**After:**
```python
class ServiceOrchestrator:
    def __init__(self):
        self.processes = {}
        self.project_root = Path(__file__).parent
        self.env = os.environ.copy()
        self.env["PYTHONPATH"] = str(self.project_root)  # Enable proper imports
```

#### 3. Process Startup
**Before:**
```python
process = subprocess.Popen(
    [sys.executable, str(module_path)],
    cwd=str(self.project_root),
    # No env passed - imports fail!
)
```

**After:**
```python
process = subprocess.Popen(
    command,
    cwd=str(self.project_root),
    env=self.env,  # Pass configured PYTHONPATH
    stdout=subprocess.PIPE,
    stderr=subprocess.STDOUT,
)
```

## Why This Works

1. **Module Import Format**: `module.path:app_object` tells uvicorn exactly which FastAPI app to run
   - Example: `aurabackend.code_generation_service.main:code_gen_app`
   - Means: Find `code_gen_app` in `aurabackend/code_generation_service/main.py`

2. **Uvicorn Execution**: FastAPI apps don't need their own `if __name__ == "__main__"` blocks
   - Uvicorn handles the ASGI server setup
   - Command: `python -m uvicorn aurabackend.code_generation_service.main:code_gen_app --host 0.0.0.0 --port 8001`

3. **PYTHONPATH Management**: Enables relative imports to work
   - Services import from `shared`, `connectors`, `safety`, etc.
   - PYTHONPATH is set to project root, so `from connectors import ...` works

4. **Actual Services Located**:
   - ✅ `aurabackend/api_gateway/enhanced_main.py` - has `app` and `if __name__` block
   - ✅ `aurabackend/code_generation_service/main.py` - has `code_gen_app`
   - ✅ `aurabackend/execution_sandbox/main.py` - has `execution_app`
   - ✅ `aurabackend/scheduler_service/main.py` - has `scheduler_app`
   - ✅ `aurabackend/orchestration_service/main.py` - has `orchestration_app`

## Testing

### Start orchestrator:
```bash
cd c:\Users\mouni\Documents\GitHub\Data-Analyst-Agent\Data-Analyst-Agent
python orchestrator.py
```

### Check if services are running:
```bash
netstat -ano | Select-String ":800"
```

Should show listening on ports: 8000, 8001, 8003, 8004, 8006

### Test a service:
```bash
Invoke-WebRequest -Uri "http://localhost:8001/health" -UseBasicParsing | Select-Object StatusCode, Content
```

Expected response: `{"status": "healthy", "service": "code_generation"}`

## Files Modified
- ✅ `orchestrator.py` - Fixed service definitions, added PYTHONPATH, switched to uvicorn
- ✅ `ORCHESTRATOR_GUIDE.md` - Created comprehensive guide with all service paths and troubleshooting

## Next Steps
1. Run `python orchestrator.py` to start all services
2. Frontend will connect via Vite proxy to `http://localhost:8000`
3. Services communicate internally on their respective ports
