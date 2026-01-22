# PHASE A VALIDATION SCRIPT
$env:PYTHONIOENCODING="utf-8"
Write-Host "================================================================================" -ForegroundColor Cyan
Write-Host "PHASE A STAGING DEPLOYMENT - VALIDATION SUITE" -ForegroundColor Cyan
Write-Host "================================================================================" -ForegroundColor Cyan
Write-Host ""

$totalTests = 0
$passedTests = 0
$failedTests = @()

# 1. SERVICE HEALTH CHECKS
Write-Host "[TEST 1] Service Health Checks" -ForegroundColor Yellow
$services = @(8000, 8001, 8002, 8003, 8004, 8005, 8006, 8007)
$serviceNames = @("API Gateway", "Orchestration", "Database", "Code Generation", "Scheduler", "Knowledge Base", "Metadata Store", "Execution Sandbox")

for ($i = 0; $i -lt $services.Count; $i++) {
    $totalTests++
    try {
        $response = Invoke-WebRequest -Uri "http://localhost:$($services[$i])/health" -UseBasicParsing -TimeoutSec 5 -ErrorAction Stop
        Write-Host "  [OK] $($serviceNames[$i]) (Port $($services[$i])): HEALTHY" -ForegroundColor Green
        $passedTests++
    } catch {
        Write-Host "  [FAIL] $($serviceNames[$i]) (Port $($services[$i])): ERROR" -ForegroundColor Red
        $failedTests += "$($serviceNames[$i]) health"
    }
}
Write-Host ""

# 2. PERFORMANCE TEST
Write-Host "[TEST 2] Performance Baseline (10 requests)" -ForegroundColor Yellow
$totalTests++
try {
    $latencies = @()
    for ($i = 1; $i -le 10; $i++) {
        $start = Get-Date
        $response = Invoke-WebRequest -Uri "http://localhost:8000/health" -UseBasicParsing -TimeoutSec 5 -ErrorAction Stop | Out-Null
        $end = Get-Date
        $latency = ($end - $start).TotalMilliseconds
        $latencies += $latency
    }
    
    $avgLatency = ($latencies | Measure-Object -Average).Average
    $sorted = $latencies | Sort-Object
    $p95Index = [math]::Floor(0.95 * $sorted.Count) - 1
    $p95Latency = $sorted[$p95Index]
    
    Write-Host "  Average Latency: $([math]::Round($avgLatency, 2))ms" -ForegroundColor Cyan
    Write-Host "  P95 Latency: $([math]::Round($p95Latency, 2))ms" -ForegroundColor Cyan
    
    if ($p95Latency -lt 1000) {
        Write-Host "  [OK] Performance: EXCELLENT (P95 less than 1000ms)" -ForegroundColor Green
        $passedTests++
    } else {
        Write-Host "  [FAIL] Performance: P95 exceeds target" -ForegroundColor Red
        $failedTests += "Performance"
    }
} catch {
    Write-Host "  [FAIL] Performance test error" -ForegroundColor Red
    $failedTests += "Performance"
}
Write-Host ""

# 3. E2E WORKFLOW TEST
Write-Host "[TEST 3] End-to-End Workflow Test" -ForegroundColor Yellow
$totalTests++
try {
    $env:PYTHONIOENCODING="utf-8"
    $output = python test_e2e_workflow.py 2>&1 | Out-String
    if ($output -match "Performance target met" -or $output -match "TOTAL TIME") {
        Write-Host "  [OK] E2E Workflow: PASSED" -ForegroundColor Green
        $passedTests++
    } else {
        Write-Host "  [FAIL] E2E Workflow: FAILED" -ForegroundColor Red
        $failedTests += "E2E workflow"
    }
} catch {
    Write-Host "  [FAIL] E2E Workflow: ERROR" -ForegroundColor Red
    $failedTests += "E2E workflow"
}
Write-Host ""

# 4. FRONTEND CHECK
Write-Host "[TEST 4] Frontend Accessibility" -ForegroundColor Yellow
$totalTests++
try {
    $response = Invoke-WebRequest -Uri "http://localhost:5173" -UseBasicParsing -TimeoutSec 5 -ErrorAction Stop
    Write-Host "  [OK] Frontend: ACCESSIBLE" -ForegroundColor Green
    $passedTests++
} catch {
    Write-Host "  [FAIL] Frontend: NOT ACCESSIBLE" -ForegroundColor Red
    $failedTests += "Frontend"
}
Write-Host ""

# RESULTS
Write-Host "================================================================================" -ForegroundColor Cyan
Write-Host "PHASE A VALIDATION RESULTS" -ForegroundColor Cyan
Write-Host "================================================================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Tests Passed: $passedTests/$totalTests" -ForegroundColor $(if($passedTests -eq $totalTests){"Green"}else{"Yellow"})
Write-Host "Tests Failed: $($failedTests.Count)" -ForegroundColor $(if($failedTests.Count -eq 0){"Green"}else{"Red"})

if ($failedTests.Count -gt 0) {
    Write-Host "`nFailed Tests:" -ForegroundColor Red
    foreach ($test in $failedTests) {
        Write-Host "  - $test" -ForegroundColor Red
    }
}

Write-Host ""
Write-Host "================================================================================" -ForegroundColor Cyan
if ($passedTests -eq $totalTests) {
    Write-Host "PHASE A VALIDATION: PASSED" -ForegroundColor Green
    Write-Host "All systems operational. Ready for Phase B Canary Deployment." -ForegroundColor Green
} else {
    Write-Host "PHASE A VALIDATION: FAILED" -ForegroundColor Red
    Write-Host "Critical issues detected. Please remediate before proceeding." -ForegroundColor Red
}
Write-Host "================================================================================" -ForegroundColor Cyan
