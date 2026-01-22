# Start Scheduler Service
# Runs the automated job scheduling service

Write-Host "Starting AURA Scheduler Service..." -ForegroundColor Cyan

# Set environment variables
$env:SCHEDULER_DATABASE_URL = "sqlite+aiosqlite:///data/scheduler.db"
$env:DATABASE_SERVICE_URL = "http://localhost:8002"
$env:SCHEDULER_PORT = "8004"
$env:SCHEDULER_CHECK_INTERVAL = "60"  # Check for jobs every 60 seconds

# Navigate to the project root (parent of aurabackend)
Set-Location -Path "$PSScriptRoot"

# Run the service as a module
Write-Host "Scheduler Service starting on port 8004..." -ForegroundColor Green
Write-Host "Worker checking for jobs every 60 seconds" -ForegroundColor Yellow
Write-Host "Press Ctrl+C to stop" -ForegroundColor Yellow
Write-Host ""

python -m uvicorn aurabackend.scheduler_service.main:app --host 0.0.0.0 --port 8004 --reload
