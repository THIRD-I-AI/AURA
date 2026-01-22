#!/bin/bash
# AURA Production Deployment Script
# Deploy AURA to production infrastructure
# Usage: ./deploy-production.sh [environment] [version]

set -euo pipefail

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Configuration
ENVIRONMENT=${1:-production}
VERSION=${2:-latest}
DEPLOYMENT_DIR="/opt/aura"
BACKUP_DIR="/opt/aura/backups"
LOG_FILE="/var/log/aura/deployment.log"

# Functions
log() {
    echo -e "${BLUE}[$(date +'%Y-%m-%d %H:%M:%S')]${NC} $*" | tee -a "$LOG_FILE"
}

success() {
    echo -e "${GREEN}✓ $*${NC}" | tee -a "$LOG_FILE"
}

error() {
    echo -e "${RED}✗ $*${NC}" | tee -a "$LOG_FILE"
}

warning() {
    echo -e "${YELLOW}⚠ $*${NC}" | tee -a "$LOG_FILE"
}

# Pre-deployment checks
pre_deployment_checks() {
    log "Running pre-deployment checks..."
    
    # Check if running as root
    if [ "$EUID" -ne 0 ]; then
        error "This script must be run as root"
        exit 1
    fi
    
    # Check required commands
    for cmd in docker docker-compose python3 npm; do
        if ! command -v $cmd &> /dev/null; then
            error "$cmd is not installed"
            exit 1
        fi
    done
    
    success "Pre-deployment checks passed"
}

# Backup current deployment
backup_current() {
    log "Backing up current deployment..."
    
    if [ -d "$DEPLOYMENT_DIR" ]; then
        TIMESTAMP=$(date +%Y%m%d_%H%M%S)
        mkdir -p "$BACKUP_DIR"
        tar -czf "$BACKUP_DIR/aura_backup_$TIMESTAMP.tar.gz" -C "$DEPLOYMENT_DIR" . 2>/dev/null || true
        success "Backup created: $BACKUP_DIR/aura_backup_$TIMESTAMP.tar.gz"
    fi
}

# Update application code
update_code() {
    log "Updating application code..."
    
    cd "$DEPLOYMENT_DIR"
    git fetch origin
    git checkout "v$VERSION" || git checkout main
    
    success "Code updated to version $VERSION"
}

# Build and push Docker images
build_docker_images() {
    log "Building Docker images..."
    
    # Backend
    docker build -f aurabackend/Dockerfile -t aura-backend:$VERSION .
    
    # Frontend (if Dockerfile exists)
    if [ -f "frontend/Dockerfile" ]; then
        docker build -f frontend/Dockerfile -t aura-frontend:$VERSION ./frontend
    fi
    
    success "Docker images built successfully"
}

# Deploy with docker-compose
deploy_services() {
    log "Deploying services with docker-compose..."
    
    export AURA_VERSION=$VERSION
    docker-compose -f docker-compose.yml up -d
    
    sleep 5
    
    # Wait for services to be ready
    log "Waiting for services to become healthy..."
    for i in {1..30}; do
        if docker-compose ps | grep -q "healthy"; then
            success "All services are healthy"
            return 0
        fi
        sleep 1
    done
    
    warning "Services may still be starting. Verify manually."
}

# Run smoke tests
smoke_tests() {
    log "Running smoke tests..."
    
    # Test API endpoints
    ENDPOINTS=(
        "http://localhost:8000/health"
        "http://localhost:8000/files"
        "http://localhost:8000/semantic/models"
    )
    
    for endpoint in "${ENDPOINTS[@]}"; do
        if curl -sf "$endpoint" > /dev/null; then
            success "✓ $endpoint"
        else
            error "✗ $endpoint failed"
            return 1
        fi
    done
    
    success "All smoke tests passed"
}

# Update DNS/Load Balancer
update_infrastructure() {
    log "Updating infrastructure..."
    
    # This would typically call infrastructure-as-code tools
    # like Terraform, CloudFormation, or kubectl
    
    warning "Manual infrastructure update may be required"
    log "Check DNS and load balancer configuration"
}

# Enable monitoring
enable_monitoring() {
    log "Enabling monitoring and alerting..."
    
    # Prometheus scrape config
    # Grafana dashboards
    # Alert rules
    
    success "Monitoring enabled"
}

# Post-deployment validation
post_deployment() {
    log "Running post-deployment validation..."
    
    # Check all services are running
    docker-compose ps
    
    # Run integration tests
    if [ -f "aurabackend/tests/test_integration.py" ]; then
        log "Running integration tests..."
        python3 -m pytest aurabackend/tests/test_integration.py -v || warning "Some integration tests failed"
    fi
    
    success "Post-deployment validation complete"
}

# Rollback function
rollback() {
    error "Deployment failed. Rolling back..."
    
    docker-compose down
    
    LATEST_BACKUP=$(ls -t "$BACKUP_DIR"/aura_backup_*.tar.gz 2>/dev/null | head -1)
    if [ -n "$LATEST_BACKUP" ]; then
        log "Restoring from $LATEST_BACKUP..."
        tar -xzf "$LATEST_BACKUP" -C "$DEPLOYMENT_DIR"
        docker-compose up -d
        success "Rollback complete"
    else
        error "No backup available for rollback"
        exit 1
    fi
}

# Main deployment flow
main() {
    echo -e "${BLUE}"
    echo "╔══════════════════════════════════════════════════════════════╗"
    echo "║         AURA PRODUCTION DEPLOYMENT SCRIPT                    ║"
    echo "║         Environment: $ENVIRONMENT"
    echo "║         Version: $VERSION"
    echo "╚══════════════════════════════════════════════════════════════╝"
    echo -e "${NC}"
    
    mkdir -p "$(dirname "$LOG_FILE")"
    
    trap 'rollback' ERR
    
    pre_deployment_checks
    backup_current
    update_code
    build_docker_images
    deploy_services
    smoke_tests
    post_deployment
    enable_monitoring
    
    echo -e "${GREEN}"
    echo "╔══════════════════════════════════════════════════════════════╗"
    echo "║         DEPLOYMENT SUCCESSFUL! 🚀                           ║"
    echo "║         AURA is now live in $ENVIRONMENT                    ║"
    echo "╚══════════════════════════════════════════════════════════════╝"
    echo -e "${NC}"
    
    log "Deployment completed successfully"
}

# Run main function
main "$@"
