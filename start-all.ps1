# AURA - Start All Services
# Comprehensive startup script for all AURA backend services

Write-Host "================================================================================" -ForegroundColor Cyan
Write-Host "AURA - Enterprise Data Analysis Platform" -ForegroundColor Cyan
Write-Host "================================================================================" -ForegroundColor Cyan
Write-Host ""

# Set project path
$projectPath = $PSScriptRoot
Set-Location $projectPath

Write-Host ""
Write-Host "Starting Backend Services..." -ForegroundColor Green
Write-Host "================================================================================" -ForegroundColor Gray
Write-Host ""

# 1. Database Service (Port 8002)
Write-Host "1. Starting Database Service (Port 8002)..." -ForegroundColor Yellow
$cmd1 = "Set-Location '$projectPath\aurabackend'; `$env:PYTHONPATH='$projectPath\aurabackend'; Write-Host 'Database Service Starting...' -ForegroundColor Cyan; python -m uvicorn database.main:app --host 0.0.0.0 --port 8002 --reload"
Start-Process powershell -ArgumentList "-NoExit", "-Command", $cmd1
Start-Sleep -Seconds 2

# 2. Scheduler Service (Port 8004)
Write-Host "2. Starting Scheduler Service (Port 8004)..." -ForegroundColor Yellow
$cmd2 = "Set-Location '$projectPath\aurabackend'; `$env:PYTHONPATH='$projectPath\aurabackend'; `$env:SCHEDULER_DATABASE_URL='sqlite+aiosqlite:///data/scheduler.db'; `$env:DATABASE_SERVICE_URL='http://localhost:8002'; Write-Host 'Scheduler Service Starting...' -ForegroundColor Cyan; python -m uvicorn scheduler_service.main:app --host 0.0.0.0 --port 8004 --reload"
Start-Process powershell -ArgumentList "-NoExit", "-Command", $cmd2
Start-Sleep -Seconds 2

# 3. Orchestration Service (Port 8001)
Write-Host "3. Starting Orchestration Service (Port 8001)..." -ForegroundColor Yellow
$cmd3 = "Set-Location '$projectPath\aurabackend'; `$env:PYTHONPATH='$projectPath\aurabackend'; Write-Host 'Orchestration Service Starting...' -ForegroundColor Cyan; python -m uvicorn orchestration_service.main:app --host 0.0.0.0 --port 8001 --reload"
Start-Process powershell -ArgumentList "-NoExit", "-Command", $cmd3
Start-Sleep -Seconds 2

# 4. API Gateway (Port 8000)
Write-Host "4. Starting API Gateway (Port 8000)..." -ForegroundColor Yellow
$cmd4 = "Set-Location '$projectPath\aurabackend'; `$env:PYTHONPATH='$projectPath\aurabackend'; Write-Host 'API Gateway Starting...' -ForegroundColor Cyan; python -m uvicorn api_gateway.main:api_gateway --host 0.0.0.0 --port 8000 --reload"
Start-Process powershell -ArgumentList "-NoExit", "-Command", $cmd4
Start-Sleep -Seconds 2

# 5. Code Generation Service (Port 8003)
Write-Host "5. Starting Code Generation Service (Port 8003)..." -ForegroundColor Yellow
$cmd5 = "Set-Location '$projectPath\aurabackend'; `$env:PYTHONPATH='$projectPath\aurabackend'; Write-Host 'Code Generation Service Starting...' -ForegroundColor Cyan; python -m uvicorn code_generation_service.main:code_gen_app --host 0.0.0.0 --port 8003 --reload"
Start-Process powershell -ArgumentList "-NoExit", "-Command", $cmd5
Start-Sleep -Seconds 1

# 6. Execution Sandbox (Port 8007)
Write-Host "6. Starting Execution Sandbox (Port 8007)..." -ForegroundColor Yellow
$cmd6 = "Set-Location '$projectPath\aurabackend'; `$env:PYTHONPATH='$projectPath\aurabackend'; Write-Host 'Execution Sandbox Starting...' -ForegroundColor Cyan; python -m uvicorn execution_sandbox.main:execution_app --host 0.0.0.0 --port 8007 --reload"
Start-Process powershell -ArgumentList "-NoExit", "-Command", $cmd6
Start-Sleep -Seconds 1

# 7. Knowledge Base (Port 8005)
Write-Host "7. Starting Knowledge Base (Port 8005)..." -ForegroundColor Yellow
$cmd7 = "Set-Location '$projectPath\aurabackend'; `$env:PYTHONPATH='$projectPath\aurabackend'; Write-Host 'Knowledge Base Starting...' -ForegroundColor Cyan; python -m uvicorn knowledge_base.main:kb_app --host 0.0.0.0 --port 8005 --reload"
Start-Process powershell -ArgumentList "-NoExit", "-Command", $cmd7
Start-Sleep -Seconds 1

# 8. Metadata Store (Port 8006)
Write-Host "8. Starting Metadata Store (Port 8006)..." -ForegroundColor Yellow
$cmd8 = "Set-Location '$projectPath\aurabackend'; `$env:PYTHONPATH='$projectPath\aurabackend'; Write-Host 'Metadata Store Starting...' -ForegroundColor Cyan; python -m uvicorn metadata_store.main:metadata_app --host 0.0.0.0 --port 8006 --reload"
Start-Process powershell -ArgumentList "-NoExit", "-Command", $cmd8

Write-Host ""
Write-Host "================================================================================" -ForegroundColor Gray
Write-Host ""
Write-Host "Waiting for services to initialize..." -ForegroundColor Yellow
Start-Sleep -Seconds 8

Write-Host ""
Write-Host "================================================================================" -ForegroundColor Gray
Write-Host ""
Write-Host "AURA Backend Services Launched!" -ForegroundColor Green
Write-Host ""
Write-Host "Service URLs:" -ForegroundColor Cyan
Write-Host "   Database Service:      http://localhost:8002/docs" -ForegroundColor White
Write-Host "   API Gateway:           http://localhost:8000/" -ForegroundColor White
Write-Host "   Orchestration:         http://localhost:8001/docs" -ForegroundColor White
Write-Host "   Code Generation:       http://localhost:8003/docs" -ForegroundColor White
Write-Host "   Scheduler Service:     http://localhost:8004/docs" -ForegroundColor White
Write-Host "   Knowledge Base:        http://localhost:8005/docs" -ForegroundColor White
Write-Host "   Metadata Store:        http://localhost:8006/docs" -ForegroundColor White
Write-Host "   Execution Sandbox:     http://localhost:8007/docs" -ForegroundColor White
Write-Host ""
Write-Host "Next Steps:" -ForegroundColor Cyan
Write-Host "   1. Start frontend: cd frontend && npm run dev" -ForegroundColor White
Write-Host "   2. Access AURA UI: http://localhost:5173" -ForegroundColor White
Write-Host ""
Write-Host "Tips:" -ForegroundColor Cyan
Write-Host "   - All services run in separate PowerShell windows" -ForegroundColor Gray
Write-Host "   - Close individual windows to stop services" -ForegroundColor Gray
Write-Host ""
Write-Host "================================================================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Press any key to exit this launcher..." -ForegroundColor Gray
$null = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")
