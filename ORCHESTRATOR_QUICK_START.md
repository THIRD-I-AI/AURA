# 🚀 Quick Start: Orchestrator

## What Was Fixed
✅ Corrected 5 service module paths  
✅ Added PYTHONPATH configuration  
✅ Switched to uvicorn launch method  
✅ 3 bad paths removed (non-existent services)  

## Start Backend Services
```bash
cd c:\Users\mouni\Documents\GitHub\Data-Analyst-Agent\Data-Analyst-Agent
python orchestrator.py
```

## Service Mapping (Verified)

| Service | Port | Module |
|---------|------|--------|
| API Gateway | 8000 | `aurabackend.api_gateway.enhanced_main:app` |
| Code Gen | 8001 | `aurabackend.code_generation_service.main:code_gen_app` |
| Execution | 8003 | `aurabackend.execution_sandbox.main:execution_app` |
| Scheduler | 8004 | `aurabackend.scheduler_service.main:scheduler_app` |
| Orchestration | 8006 | `aurabackend.orchestration_service.main:orchestration_app` |

## Files Changed
- ✅ `orchestrator.py` - Fixed with correct paths & PYTHONPATH
- ✅ `ORCHESTRATOR_GUIDE.md` - Full documentation
- ✅ `ORCHESTRATOR_FIX_SUMMARY.md` - Technical details
- ✅ `SERVICE_ENTRY_POINTS_VERIFIED.md` - Service verification
- ✅ `ORCHESTRATOR_READY.md` - Complete summary

## Stop Services
Press `Ctrl+C` in the orchestrator terminal

## Verify Services Running
```bash
netstat -ano | Select-String ":800"
```

Should show 5 listening ports: 8000, 8001, 8003, 8004, 8006

## Test a Service
```bash
Invoke-WebRequest -Uri "http://localhost:8001/health" -UseBasicParsing
```

Response:
```json
{"status": "healthy", "service": "code_generation"}
```

## Frontend Connection
Already configured to proxy:
- `POST /api/query` → `http://localhost:8000/query`
- Vite config routes `/api` to port 8000 automatically

## Run Frontend
```bash
cd c:\Users\mouni\Documents\GitHub\Data-Analyst-Agent\Data-Analyst-Agent\frontend
npm run dev
```

## Common Issues

**Ports in use?**
```bash
Get-Process python | Stop-Process -Force
Start-Sleep -Seconds 2
python orchestrator.py  # Try again
```

**Module not found?**
Orchestrator automatically sets PYTHONPATH. If issues persist, ensure:
```bash
pip install -r aurabackend/requirements.txt
```

**Uvicorn missing?**
```bash
pip install uvicorn[standard]
```

---

**Status: ✅ READY FOR TESTING**

Next: Run `python orchestrator.py` and verify all 5 services start.
