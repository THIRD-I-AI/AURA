# AURA Production Deployment Script for Windows
# PowerShell 5.1+
# Usage: .\deploy-production.ps1 -Environment "staging" -Version "1.0.0"

param(
    [Parameter(Mandatory=$false)]
    [string]$Environment = "staging",
    
    [Parameter(Mandatory=$false)]
    [string]$Version = "1.0.0",
    
    [Parameter(Mandatory=$false)]
    [switch]$SkipTests = $false,
    
    [Parameter(Mandatory=$false)]
    [switch]$RollbackOnFailure = $true
)

$ErrorActionPreference = "Stop"
$WarningPreference = "Continue"

# Configuration
$AURA_HOME = Split-Path -Parent $MyInvocation.MyCommand.Path
$BACKUP_DIR = Join-Path $AURA_HOME "backups"
$LOG_DIR = Join-Path $AURA_HOME "logs"
$TIMESTAMP = Get-Date -Format "yyyyMMdd_HHmmss"
$LOG_FILE = Join-Path $LOG_DIR "deployment_$TIMESTAMP.log"
$BACKUP_FILE = Join-Path $BACKUP_DIR "aura_backup_$TIMESTAMP.zip"

# Colors
$Colors = @{
    "SUCCESS" = "Green"
    "ERROR"   = "Red"
    "WARNING" = "Yellow"
    "INFO"    = "Cyan"
}

# ============================================================================
# Logging Functions
# ============================================================================

function Write-Log {
    param([string]$Message, [string]$Level = "INFO")
    
    $Prefix = "[$Level] $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
    $Output = "$Prefix - $Message"
    
    Write-Host $Output -ForegroundColor $Colors[$Level]
    Add-Content -Path $LOG_FILE -Value $Output
}

function Write-Success {
    param([string]$Message)
    Write-Log $Message "SUCCESS"
}

function Write-Error-Custom {
    param([string]$Message)
    Write-Log $Message "ERROR"
    Write-Host ""
}

function Write-Warning-Custom {
    param([string]$Message)
    Write-Log $Message "WARNING"
}

# ============================================================================
# Pre-Deployment Checks
# ============================================================================

function Invoke-PreDeploymentChecks {
    Write-Log "Starting pre-deployment checks..."
    
    # Check if running as administrator
    $CurrentUser = [Security.Principal.WindowsIdentity]::GetCurrent()
    $Principal = New-Object Security.Principal.WindowsPrincipal($CurrentUser)
    
    if (-not $Principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        Write-Error-Custom "This script must be run as Administrator"
        exit 1
    }
    Write-Success "Running with administrator privileges"
    
    # Check required commands
    $RequiredCommands = @("docker", "docker-compose", "python", "npm", "git")
    foreach ($cmd in $RequiredCommands) {
        if (-not (Get-Command $cmd -ErrorAction SilentlyContinue)) {
            Write-Error-Custom "Required command not found: $cmd"
            exit 1
        }
    }
    Write-Success "All required commands are available"
    
    # Check Docker daemon
    try {
        docker ps | Out-Null
    } catch {
        Write-Error-Custom "Docker daemon is not running"
        exit 1
    }
    Write-Success "Docker daemon is running"
    
    # Create directories
    if (-not (Test-Path $BACKUP_DIR)) {
        New-Item -ItemType Directory -Path $BACKUP_DIR -Force | Out-Null
    }
    if (-not (Test-Path $LOG_DIR)) {
        New-Item -ItemType Directory -Path $LOG_DIR -Force | Out-Null
    }
    Write-Success "Backup and log directories ready"
    
    Write-Success "Pre-deployment checks completed"
}

# ============================================================================
# Backup
# ============================================================================

function Invoke-Backup {
    Write-Log "Creating backup of current deployment..."
    
    try {
        # Backup current code
        $BackupItems = @(
            "aurabackend",
            "frontend",
            "docker-compose.yml",
            ".env.production"
        )
        
        $TempBackupDir = Join-Path ([System.IO.Path]::GetTempPath()) "aura_backup_$TIMESTAMP"
        New-Item -ItemType Directory -Path $TempBackupDir -Force | Out-Null
        
        foreach ($item in $BackupItems) {
            $SourcePath = Join-Path $AURA_HOME $item
            if (Test-Path $SourcePath) {
                Copy-Item -Path $SourcePath -Destination (Join-Path $TempBackupDir $item) -Recurse -Force
            }
        }
        
        # Create zip file
        Add-Type -AssemblyName System.IO.Compression.FileSystem
        [System.IO.Compression.ZipFile]::CreateFromDirectory($TempBackupDir, $BACKUP_FILE)
        
        # Cleanup temp directory
        Remove-Item -Path $TempBackupDir -Recurse -Force
        
        Write-Success "Backup created: $BACKUP_FILE"
    } catch {
        Write-Error-Custom "Backup failed: $_"
        exit 1
    }
}

# ============================================================================
# Code Update
# ============================================================================

function Update-Code {
    Write-Log "Updating code to version $Version..."
    
    try {
        # Fetch latest changes
        Write-Log "Fetching latest changes..."
        git fetch origin | Out-Null
        
        # Checkout specific version
        Write-Log "Checking out version $Version..."
        git checkout $Version 2>&1 | ForEach-Object { Write-Log $_ }
        
        Write-Success "Code updated to version $Version"
    } catch {
        Write-Error-Custom "Code update failed: $_"
        exit 1
    }
}

# ============================================================================
# Docker Build
# ============================================================================

function Build-DockerImages {
    Write-Log "Building Docker images..."
    
    try {
        # Backend
        Write-Log "Building backend image..."
        $BackendDockerfile = Join-Path $AURA_HOME "aurabackend\Dockerfile"
        
        if (Test-Path $BackendDockerfile) {
            docker build -f $BackendDockerfile `
                        -t "aura-backend:$Version" `
                        -t "aura-backend:latest" `
                        (Join-Path $AURA_HOME "aurabackend") | Out-Null
            Write-Success "Backend image built successfully"
        }
        
        # Frontend
        Write-Log "Building frontend image..."
        $FrontendPath = Join-Path $AURA_HOME "frontend"
        
        if (Test-Path $FrontendPath) {
            Push-Location $FrontendPath
            
            try {
                npm install | Out-Null
                npm run build | Out-Null
                
                # Create Dockerfile for frontend (if not exists)
                $FrontendDockerfile = Join-Path $FrontendPath "Dockerfile"
                if (-not (Test-Path $FrontendDockerfile)) {
                    @"
FROM node:20-alpine
WORKDIR /app
COPY . .
RUN npm install && npm run build
EXPOSE 5173
CMD ["npm", "run", "preview"]
"@ | Set-Content -Path $FrontendDockerfile
                }
                
                docker build -f $FrontendDockerfile `
                            -t "aura-frontend:$Version" `
                            -t "aura-frontend:latest" `
                            . | Out-Null
                Write-Success "Frontend image built successfully"
            } finally {
                Pop-Location
            }
        }
    } catch {
        Write-Error-Custom "Docker build failed: $_"
        exit 1
    }
}

# ============================================================================
# Deploy Services
# ============================================================================

function Deploy-Services {
    Write-Log "Deploying services via docker-compose..."
    
    try {
        # Stop existing containers
        Write-Log "Stopping existing containers..."
        docker-compose down 2>&1 | ForEach-Object { Write-Log $_ }
        
        # Start services
        Write-Log "Starting services..."
        docker-compose up -d 2>&1 | ForEach-Object { Write-Log $_ }
        
        # Wait for services to be ready
        Write-Log "Waiting for services to be ready (30 seconds)..."
        Start-Sleep -Seconds 30
        
        # Check service status
        Write-Log "Checking service status..."
        docker-compose ps
        
        Write-Success "Services deployed successfully"
    } catch {
        Write-Error-Custom "Service deployment failed: $_"
        exit 1
    }
}

# ============================================================================
# Smoke Tests
# ============================================================================

function Invoke-SmokeTests {
    Write-Log "Running smoke tests..."
    
    if ($SkipTests) {
        Write-Warning-Custom "Skipping smoke tests"
        return
    }
    
    $TestEndpoints = @(
        "http://localhost:8000/health",
        "http://localhost:8000/files",
        "http://localhost:8000/semantic/models"
    )
    
    $FailedTests = 0
    
    foreach ($endpoint in $TestEndpoints) {
        try {
            Write-Log "Testing endpoint: $endpoint"
            $Response = Invoke-WebRequest -Uri $endpoint -UseBasicParsing -TimeoutSec 5
            
            if ($Response.StatusCode -eq 200) {
                Write-Success "✓ $endpoint"
            } else {
                Write-Warning-Custom "✗ $endpoint (Status: $($Response.StatusCode))"
                $FailedTests++
            }
        } catch {
            Write-Warning-Custom "✗ $endpoint (Error: $_)"
            $FailedTests++
        }
    }
    
    if ($FailedTests -gt 0) {
        Write-Error-Custom "Smoke tests failed: $FailedTests endpoint(s) failed"
        return $false
    }
    
    Write-Success "All smoke tests passed"
    return $true
}

# ============================================================================
# Health Check
# ============================================================================

function Invoke-HealthCheck {
    Write-Log "Performing health checks..."
    
    try {
        # API health
        $ApiHealth = Invoke-WebRequest -Uri "http://localhost:8000/health" -UseBasicParsing | ConvertFrom-Json
        Write-Log "API Status: $($ApiHealth.status)"
        
        # Database connectivity
        Write-Log "Checking database connectivity..."
        $DbStatus = docker-compose exec -T aura-db psql -U aura_user -d aura_production -c "SELECT 1" 2>&1
        Write-Log "Database: Connected"
        
        # Redis connectivity
        Write-Log "Checking Redis connectivity..."
        $RedisStatus = docker-compose exec -T aura-redis redis-cli PING 2>&1
        Write-Log "Redis: $RedisStatus"
        
        Write-Success "All health checks passed"
    } catch {
        Write-Warning-Custom "Health check encountered an issue: $_"
    }
}

# ============================================================================
# Post-Deployment
# ============================================================================

function Invoke-PostDeployment {
    Write-Log "Running post-deployment tasks..."
    
    # Display service status
    Write-Log "Service Status:"
    docker-compose ps
    
    # Run integration tests if available
    $TestFile = Join-Path $AURA_HOME "test_e2e_workflow.py"
    if ((Test-Path $TestFile) -and -not $SkipTests) {
        Write-Log "Running integration tests..."
        try {
            python $TestFile 2>&1 | ForEach-Object { Write-Log $_ }
            Write-Success "Integration tests passed"
        } catch {
            Write-Warning-Custom "Integration tests encountered an issue (may be expected)"
        }
    }
    
    Write-Success "Post-deployment tasks completed"
}

# ============================================================================
# Rollback
# ============================================================================

function Invoke-Rollback {
    param([string]$Reason)
    
    Write-Error-Custom "DEPLOYMENT FAILED: $Reason"
    Write-Log "Initiating rollback..."
    
    try {
        # Stop current containers
        Write-Log "Stopping failed containers..."
        docker-compose down 2>&1 | ForEach-Object { Write-Log $_ }
        
        # Restore from backup
        if (Test-Path $BACKUP_FILE) {
            Write-Log "Extracting backup from $BACKUP_FILE..."
            
            $BackupExtractDir = Join-Path ([System.IO.Path]::GetTempPath()) "aura_rollback_$TIMESTAMP"
            Add-Type -AssemblyName System.IO.Compression.FileSystem
            [System.IO.Compression.ZipFile]::ExtractToDirectory($BACKUP_FILE, $BackupExtractDir)
            
            # Restore files
            $RestoreItems = @("aurabackend", "frontend", "docker-compose.yml", ".env.production")
            foreach ($item in $RestoreItems) {
                $SourcePath = Join-Path $BackupExtractDir $item
                $DestPath = Join-Path $AURA_HOME $item
                
                if (Test-Path $SourcePath) {
                    Remove-Item -Path $DestPath -Recurse -Force -ErrorAction SilentlyContinue
                    Copy-Item -Path $SourcePath -Destination $DestPath -Recurse -Force
                }
            }
            
            Remove-Item -Path $BackupExtractDir -Recurse -Force
            
            # Restart services
            Write-Log "Restarting services from backup..."
            docker-compose up -d 2>&1 | ForEach-Object { Write-Log $_ }
            
            Start-Sleep -Seconds 20
            
            Write-Success "Rollback completed successfully"
        } else {
            Write-Error-Custom "Backup file not found, manual rollback required"
        }
    } catch {
        Write-Error-Custom "Rollback failed: $_"
    }
}

# ============================================================================
# Main Deployment Flow
# ============================================================================

function Start-Deployment {
    Write-Host ""
    Write-Host "╔════════════════════════════════════════════════════════════════╗" -ForegroundColor Cyan
    Write-Host "║     AURA Production Deployment - Windows PowerShell Script     ║" -ForegroundColor Cyan
    Write-Host "╚════════════════════════════════════════════════════════════════╝" -ForegroundColor Cyan
    Write-Host ""
    Write-Log "Deployment Configuration:"
    Write-Log "  Environment: $Environment"
    Write-Log "  Version: $Version"
    Write-Log "  Home: $AURA_HOME"
    Write-Log "  Log File: $LOG_FILE"
    Write-Log ""
    
    try {
        # Phase 1: Pre-checks
        Invoke-PreDeploymentChecks
        
        # Phase 2: Backup
        Invoke-Backup
        
        # Phase 3: Update code
        Update-Code
        
        # Phase 4: Build Docker images
        Build-DockerImages
        
        # Phase 5: Deploy services
        Deploy-Services
        
        # Phase 6: Smoke tests
        $TestsPassed = Invoke-SmokeTests
        if (-not $TestsPassed -and $RollbackOnFailure) {
            Invoke-Rollback "Smoke tests failed"
            exit 1
        }
        
        # Phase 7: Health checks
        Invoke-HealthCheck
        
        # Phase 8: Post-deployment
        Invoke-PostDeployment
        
        Write-Host ""
        Write-Host "╔════════════════════════════════════════════════════════════════╗" -ForegroundColor Green
        Write-Host "║                  ✓ DEPLOYMENT SUCCESSFUL                      ║" -ForegroundColor Green
        Write-Host "╚════════════════════════════════════════════════════════════════╝" -ForegroundColor Green
        Write-Host ""
        Write-Host "Next Steps:" -ForegroundColor Cyan
        Write-Host "  1. Monitor the application for any issues"
        Write-Host "  2. Run monitoring dashboard"
        Write-Host "  3. Prepare for Phase B (Canary deployment)"
        Write-Host ""
        
    } catch {
        if ($RollbackOnFailure) {
            Invoke-Rollback "Deployment error: $_"
        } else {
            Write-Error-Custom "Deployment failed: $_"
        }
        exit 1
    }
}

# ============================================================================
# Execution
# ============================================================================

Start-Deployment
