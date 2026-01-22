# Start Database Service
# Runs the universal database connectivity service

Write-Host "Starting AURA Database Service..." -ForegroundColor Cyan

# Set environment variables
$env:PYTHONPATH = "$PSScriptRoot\aurabackend"
$env:DATABASE_URL = "sqlite:///./aura.db"

# Navigate to the aurabackend directory
Set-Location -Path "$PSScriptRoot\aurabackend"

# Run the database service
Write-Host "Database Service starting on port 8002..." -ForegroundColor Green
Write-Host "API Documentation: http://localhost:8002/docs" -ForegroundColor Yellow
Write-Host "Press Ctrl+C to stop" -ForegroundColor Yellow
Write-Host ""

python -m uvicorn database.main:app --host 0.0.0.0 --port 8002 --reload
