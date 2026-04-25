param([switch]$Kill)

$ROOT = Split-Path -Parent $PSScriptRoot
$BACKEND = Join-Path $ROOT "aurabackend"
$envFile = Join-Path $ROOT ".env"

# Resolve virtual environment python
$VENV = Join-Path $ROOT ".venv"
if (Test-Path (Join-Path $VENV "Scripts\python.exe")) {
    $PYTHON = Join-Path $VENV "Scripts\python.exe"
    Write-Host "[env] Using .venv python: $PYTHON" -ForegroundColor DarkGray
} else {
    $PYTHON = "python"
    Write-Host "[env] No .venv found, using system python" -ForegroundColor Yellow
}

if (Test-Path $envFile) {
    Get-Content $envFile | ForEach-Object {
        if ($_ -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$' -and $_ -notmatch '^\s*#') {
            [System.Environment]::SetEnvironmentVariable($Matches[1], $Matches[2].Trim(), "Process")
        }
    }
    Write-Host "[env] Loaded .env" -ForegroundColor DarkGray
}

$env:PYTHONPATH = $BACKEND

if ($Kill) {
    Write-Host "[cleanup] Killing existing python processes..." -ForegroundColor Yellow
    Stop-Process -Name "python" -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 2
}

$services = @(
    @{ Name = "API-Gateway";       Port = 8000; Mod = "api_gateway.main:app" }
    @{ Name = "Code-Generation";   Port = 8001; Mod = "code_generation_service.main:code_gen_app" }
    @{ Name = "Connectors-Vault";  Port = 8002; Mod = "connectors.main:app" }
    @{ Name = "Exec-Sandbox";      Port = 8003; Mod = "execution_sandbox.main:execution_app" }
    @{ Name = "Scheduler";         Port = 8004; Mod = "scheduler_service.main:scheduler_app" }
    @{ Name = "Insights";          Port = 8005; Mod = "insights.main:app" }
    @{ Name = "Orchestration";     Port = 8006; Mod = "orchestration_service.main:app" }
    @{ Name = "Metadata-Store";    Port = 8007; Mod = "metadata_store.main:metadata_app" }
    @{ Name = "UASR-Service";      Port = 8009; Mod = "uasr.service:app" }
)

Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ("  AURA Backend  -  Starting {0} Services" -f $services.Count) -ForegroundColor Cyan
Write-Host ("  DB: {0}:{1}/{2}" -f $env:DB_HOST, $env:DB_PORT, $env:DB_NAME) -ForegroundColor DarkCyan
Write-Host ("  Python: {0}" -f $PYTHON) -ForegroundColor DarkCyan
Write-Host "============================================================" -ForegroundColor Cyan

foreach ($svc in $services) {
    $cmd = "Set-Location '{0}'; `$env:PYTHONPATH = '{0}'; `$env:PYTHONUNBUFFERED = '1'; `$Host.UI.RawUI.WindowTitle = 'AURA: {2}'; & '{1}' -m uvicorn {3} --host 0.0.0.0 --port {4} --reload" -f $BACKEND, $PYTHON, $svc.Name, $svc.Mod, $svc.Port
    Start-Process powershell -ArgumentList "-NoExit", "-Command", $cmd
    Write-Host ("  [+] {0} -> http://localhost:{1}" -f $svc.Name, $svc.Port) -ForegroundColor Green
}

Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  All services launching in separate windows." -ForegroundColor Cyan
Write-Host "  Wait ~5s then run:  python test_operability.py" -ForegroundColor Yellow
Write-Host "============================================================" -ForegroundColor Cyan
