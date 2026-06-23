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

# Always sweep stale AURA backends before starting — running this
# script twice (or alongside another launcher) leaves duplicate
# uvicorn instances racing for the same ports and the SQLite write
# lock on metadata.db, which deadlocks the alembic migration in the
# api_gateway lifespan. Idempotent cleanup makes the script safe to
# re-run. Only matches python processes whose command line includes
# the project root, so chroma-mcp / claude-mem / unrelated python
# is left alone.
$projectMatch = [regex]::Escape($ROOT)
$allPython = Get-CimInstance Win32_Process -Filter "Name='python.exe'"
$launchers = $allPython | Where-Object { $_.CommandLine -and $_.CommandLine -match $projectMatch }
$launcherPids = @($launchers | ForEach-Object { $_.ProcessId })
$workerPids = @()
if ($launcherPids.Count -gt 0) {
    $workers = $allPython | Where-Object {
        $_.CommandLine -and
        $_.CommandLine -match 'multiprocessing\.spawn' -and
        $launcherPids -contains $_.ParentProcessId
    }
    $workerPids = @($workers | ForEach-Object { $_.ProcessId })
}
$allPids = @($launcherPids + $workerPids) | Select-Object -Unique
if ($allPids.Count -gt 0) {
    Write-Host ("[cleanup] Replacing {0} stale AURA python processes (launchers={1}, workers={2})" -f $allPids.Count, $launcherPids.Count, $workerPids.Count) -ForegroundColor Yellow
    $allPids | ForEach-Object { Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue }
    Start-Sleep -Seconds 2
} elseif ($Kill) {
    Write-Host "[cleanup] No AURA python processes found." -ForegroundColor DarkGray
}

if ($Kill) {
    # --Kill = exit after cleanup, do not relaunch. Useful for
    # `start_all.ps1 --Kill` as a pure shutdown command.
    Write-Host "[cleanup] --Kill specified; not starting services." -ForegroundColor Yellow
    return
}

$services = @(
    @{ Name = "API-Gateway";       Port = 8000; Mod = "api_gateway.main:app" }
    @{ Name = "Code-Generation";   Port = 8001; Mod = "code_generation_service.main:code_gen_app" }
    @{ Name = "Connectors-Vault";  Port = 8002; Mod = "connectors.main:app" }
    @{ Name = "Exec-Sandbox";      Port = 8003; Mod = "execution_sandbox_service.main:execution_app" }
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
