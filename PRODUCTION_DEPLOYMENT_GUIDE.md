# 🚀 AURA Production Deployment Guide

**Version**: 1.0.0  
**Date**: January 22, 2026  
**Status**: ✅ READY FOR DEPLOYMENT

---

## Table of Contents

1. [Pre-Deployment Checklist](#pre-deployment-checklist)
2. [Infrastructure Requirements](#infrastructure-requirements)
3. [Deployment Strategy](#deployment-strategy)
4. [Step-by-Step Deployment](#step-by-step-deployment)
5. [Post-Deployment Validation](#post-deployment-validation)
6. [Monitoring & Operations](#monitoring--operations)
7. [Troubleshooting](#troubleshooting)
8. [Rollback Procedures](#rollback-procedures)

---

## Pre-Deployment Checklist

### Prerequisites
- [ ] Production environment approved
- [ ] Infrastructure provisioned (servers, databases, storage)
- [ ] DNS configured
- [ ] SSL/TLS certificates installed
- [ ] Load balancer configured
- [ ] Monitoring tools set up (Prometheus, Grafana)
- [ ] Backup system operational
- [ ] Team trained on operations
- [ ] Runbook documented

### Credentials & Secrets
- [ ] PostgreSQL admin credentials secured in vault
- [ ] BigQuery service account credentials ready
- [ ] API keys and tokens generated
- [ ] JWT secret key created
- [ ] Sentry DSN configured
- [ ] Redis password set

### Code Readiness
- [ ] All tests passing locally
- [ ] Code reviewed and approved
- [ ] Documentation updated
- [ ] Version tags created
- [ ] Release notes prepared
- [ ] Known limitations documented

### Communication Plan
- [ ] Stakeholder notifications scheduled
- [ ] Support team briefed
- [ ] Maintenance window announced
- [ ] Rollback plan communicated
- [ ] Escalation contacts listed

---

## Infrastructure Requirements

### Minimum Production Setup

```yaml
Services:
  API Gateway (Port 8000):
    - CPU: 2 cores
    - RAM: 2GB
    - Storage: 20GB
    - Replicas: 2 (for HA)
    
  Database Service (Port 8002):
    - CPU: 4 cores
    - RAM: 4GB
    - Storage: 100GB+
    
  File Service:
    - CPU: 1 core
    - RAM: 1GB
    - Storage: 500GB+ (for uploads)
    
  Orchestration (Port 8001):
    - CPU: 1 core
    - RAM: 1GB
    
  Scheduler (Port 8004):
    - CPU: 1 core
    - RAM: 1GB
    
  Execution Sandbox (Port 8007):
    - CPU: 2 cores
    - RAM: 2GB

Database:
  PostgreSQL 14+
  - Initial size: 100GB
  - Auto-scaling up to 500GB
  - Daily backups
  - Point-in-time recovery enabled

Cache:
  Redis 6+
  - 4GB RAM
  - Persistence enabled
  - Replication configured

Storage:
  Object Storage (S3/GCS/Azure Blob)
  - Upload bucket: 1TB initial
  - Backup bucket: 2TB
  - Lifecycle policies configured
```

### Recommended Cloud Providers

**AWS**:
- EC2 for services
- RDS PostgreSQL for database
- ElastiCache for Redis
- S3 for uploads
- CloudFront for CDN
- CloudWatch for monitoring

**Google Cloud**:
- Compute Engine for services
- Cloud SQL for PostgreSQL
- Memorystore for Redis
- Cloud Storage for uploads
- Cloud CDN
- Cloud Monitoring

**Azure**:
- Virtual Machines for services
- Azure Database for PostgreSQL
- Azure Cache for Redis
- Blob Storage for uploads
- Azure CDN
- Azure Monitor

---

## Deployment Strategy

### Phase A: Staging Deployment (24 hours)

**Purpose**: Validate deployment process and system behavior  
**Duration**: 24 hours  
**Metrics**: All smoke tests pass, no critical errors

**Steps**:
1. Deploy to staging environment
2. Run full test suite
3. Performance test with load
4. Security scan
5. Monitor for 24 hours
6. Collect team feedback

**Success Criteria**:
- ✅ All endpoints responding
- ✅ Database operations working
- ✅ File uploads functional
- ✅ Semantic modeling working
- ✅ No memory leaks
- ✅ Response times < 1 second

**Go/No-Go Decision Point**: Proceed to Phase B only if all criteria met

---

### Phase B: Canary Deployment (48 hours)

**Purpose**: Gradual rollout to limited user base  
**Duration**: 48 hours  
**User Base**: 5-10% of total users

**Steps**:
1. Deploy to production with 5% traffic
2. Monitor error rates and performance
3. Gradually increase traffic: 10% → 25% → 50% → 100%
4. Each step: monitor for 1 hour before next increase
5. Be ready to rollback at any stage

**Monitoring During Rollout**:
- Error rate (target: < 0.1%)
- P95 latency (target: < 1 second)
- API availability (target: 99.9%)
- Database connection pool (target: < 80% utilization)
- Memory usage (target: < 70%)

**Go/No-Go Decision Point**: Proceed to Phase C only if error rate remains low

---

### Phase C: Full Production Deployment (Week 1)

**Purpose**: Full rollout to all users  
**Duration**: 1 week  
**User Base**: 100% of users

**Steps**:
1. Complete traffic migration to production
2. Monitor closely first 72 hours
3. Address any issues immediately
4. Fine-tune based on real usage patterns
5. Gather user feedback

**Post-Deployment Tasks** (Week 1):
1. Switch database to PostgreSQL
2. Configure replication and backups
3. Set up comprehensive monitoring
4. Optimize based on usage patterns
5. Document any issues found

---

## Step-by-Step Deployment

### 1. Prepare Deployment Environment

```powershell
# On deployment machine (Windows)
cd c:\Users\mouni\Documents\GitHub\Data-Analyst-Agent\Data-Analyst-Agent

# Set environment variables
$env:ENVIRONMENT = "production"
$env:VERSION = "1.0.0"
$env:AURA_DB_PASSWORD = Read-Host -AsSecureString "Database password"
$env:AURA_SECRET_KEY = Read-Host -AsSecureString "Secret key"

# Verify environment
Write-Host "Environment: $env:ENVIRONMENT"
Write-Host "Version: $env:VERSION"
```

### 2. Build Production Images

```powershell
# Build backend
docker build -t aura-backend:1.0.0 -f aurabackend\Dockerfile .

# Build frontend
docker build -t aura-frontend:1.0.0 -f frontend\Dockerfile ./frontend

# Tag for registry
docker tag aura-backend:1.0.0 registry.example.com/aura-backend:1.0.0
docker tag aura-frontend:1.0.0 registry.example.com/aura-frontend:1.0.0

# Push to registry
docker push registry.example.com/aura-backend:1.0.0
docker push registry.example.com/aura-frontend:1.0.0
```

### 3. Deploy Infrastructure

```bash
# Linux/Mac deployment
chmod +x deploy-production.sh
./deploy-production.sh production 1.0.0
```

**Or using Docker Compose**:

```bash
# Set version
export AURA_VERSION=1.0.0

# Start all services
docker-compose -f docker-compose.yml up -d

# Verify services
docker-compose ps

# Check logs
docker-compose logs -f
```

**Or using Kubernetes**:

```bash
# Create namespace
kubectl create namespace aura-prod

# Create secrets
kubectl create secret generic aura-secrets \
  --from-literal=db-password=$DB_PASSWORD \
  --from-literal=secret-key=$SECRET_KEY \
  -n aura-prod

# Deploy
kubectl apply -f k8s/production/ -n aura-prod

# Verify
kubectl get pods -n aura-prod
kubectl get svc -n aura-prod
```

### 4. Database Setup

```bash
# Connect to PostgreSQL
psql -h db.prod.internal -U postgres

# Create database
CREATE DATABASE aura_production ENCODING 'UTF8';

# Create user
CREATE USER aura_user WITH PASSWORD 'secure_password';

# Grant permissions
GRANT ALL PRIVILEGES ON DATABASE aura_production TO aura_user;

# Initialize schema
psql -h db.prod.internal -U aura_user -d aura_production < aurabackend/database/schema.sql

# Verify tables
\dt
```

### 5. Configure Load Balancer

```nginx
# Example Nginx configuration
upstream aura_backend {
    server api-1.prod.internal:8000;
    server api-2.prod.internal:8000;
    server api-3.prod.internal:8000;
    keepalive 32;
}

server {
    listen 443 ssl http2;
    server_name api.aura.prod.internal;

    ssl_certificate /etc/ssl/certs/aura.crt;
    ssl_certificate_key /etc/ssl/private/aura.key;

    location / {
        proxy_pass http://aura_backend;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_connect_timeout 60s;
        proxy_send_timeout 60s;
        proxy_read_timeout 60s;
    }

    location /health {
        access_log off;
        proxy_pass http://aura_backend;
    }
}
```

---

## Post-Deployment Validation

### 1. Smoke Tests

```bash
# Test API endpoints
curl -H "Authorization: Bearer $TOKEN" https://api.aura.prod.internal/health
curl -H "Authorization: Bearer $TOKEN" https://api.aura.prod.internal/files
curl -H "Authorization: Bearer $TOKEN" https://api.aura.prod.internal/semantic/models

# Test file upload
curl -X POST https://api.aura.prod.internal/files/upload \
  -F "file=@test_data.csv" \
  -H "Authorization: Bearer $TOKEN"

# Test semantic model generation
curl -X POST https://api.aura.prod.internal/semantic/models/from-file/test-file-id \
  -H "Authorization: Bearer $TOKEN"

# Test SQL validation
curl -X POST https://api.aura.prod.internal/safety/validate \
  -H "Content-Type: application/json" \
  -d '{"query":"SELECT * FROM data LIMIT 100"}' \
  -H "Authorization: Bearer $TOKEN"
```

### 2. Verify Services

```bash
# Check all services are healthy
for port in 8000 8001 8002 8004 8007; do
    echo "Checking port $port..."
    curl -s http://localhost:$port/health | jq .
done

# Check database connectivity
psql -h db.prod.internal -U aura_user -d aura_production -c "SELECT version();"

# Check Redis connectivity
redis-cli -h cache.prod.internal PING

# Check file storage
aws s3 ls s3://aura-uploads-prod/
```

### 3. Database Health

```sql
-- Check table sizes
SELECT schemaname, tablename, pg_size_pretty(pg_total_relation_size(schemaname||'.'||tablename)) 
FROM pg_tables 
ORDER BY pg_total_relation_size(schemaname||'.'||tablename) DESC;

-- Check slow queries
SELECT query, calls, mean_time 
FROM pg_stat_statements 
ORDER BY mean_time DESC LIMIT 10;

-- Check replication status (if using streaming replication)
SELECT * FROM pg_stat_replication;
```

---

## Monitoring & Operations

### Enable Prometheus Scraping

```yaml
# prometheus.yml
global:
  scrape_interval: 15s

scrape_configs:
  - job_name: 'aura-api'
    static_configs:
      - targets: ['localhost:8000']
  
  - job_name: 'aura-database'
    static_configs:
      - targets: ['localhost:8002']
  
  - job_name: 'postgres'
    static_configs:
      - targets: ['db.prod.internal:9187']
```

### Configure Grafana Dashboards

**Key Dashboards**:
1. System Overview (CPU, Memory, Disk, Network)
2. API Performance (Request rate, latency, error rate)
3. Database Health (Connections, queries, replication)
4. Business Metrics (Files uploaded, models generated, queries run)
5. Alerts (Error rates, latency spikes, capacity warnings)

### Alert Rules

```yaml
groups:
  - name: aura-alerts
    rules:
      # High error rate
      - alert: HighErrorRate
        expr: rate(http_requests_total{status=~"5.."}[5m]) > 0.05
        for: 5m
        annotations:
          summary: "High error rate detected"

      # High latency
      - alert: HighLatency
        expr: histogram_quantile(0.95, http_request_duration_seconds) > 1
        for: 5m
        annotations:
          summary: "P95 latency above 1 second"

      # Database connection pool nearly full
      - alert: DBConnectionPoolNearFull
        expr: db_connection_pool_usage > 0.8
        for: 5m
        annotations:
          summary: "Database connection pool > 80% utilized"

      # Low disk space
      - alert: LowDiskSpace
        expr: node_filesystem_avail_bytes{mountpoint="/"} < 10737418240  # 10GB
        for: 5m
        annotations:
          summary: "Less than 10GB disk space remaining"
```

---

## Troubleshooting

### Services Not Starting

**Symptom**: Services fail to start or keep crashing

**Diagnosis**:
```bash
# Check logs
docker-compose logs aura-api
journalctl -u aura-api -n 100

# Check configuration
cat .env.production

# Verify dependencies
docker ps
psql -c "SELECT 1"
redis-cli PING
```

**Solutions**:
- Verify all environment variables are set correctly
- Check database connectivity
- Ensure sufficient system resources
- Review recent code changes

### High Latency

**Symptom**: API responses slow (> 1 second)

**Diagnosis**:
```bash
# Check database slow queries
psql -c "SELECT query, calls, mean_time FROM pg_stat_statements 
  ORDER BY mean_time DESC LIMIT 10;"

# Check system resources
top
free -h
df -h

# Check API metrics
curl http://localhost:8000/metrics | grep http_request_duration
```

**Solutions**:
- Add database indexes
- Increase cache TTL
- Scale horizontally (add more API instances)
- Optimize slow queries

### Memory Leaks

**Symptom**: Memory usage continuously increases

**Diagnosis**:
```bash
# Monitor memory over time
docker stats aura-api

# Check Python memory
python -m memory_profiler aurabackend/api_gateway/main.py
```

**Solutions**:
- Check for circular references in code
- Verify file handles are closed
- Review async context managers
- Enable memory profiling in debug mode

### Database Connection Issues

**Symptom**: "Connection refused" or connection pool errors

**Diagnosis**:
```bash
# Test connectivity
psql -h db.prod.internal -U aura_user -d aura_production -c "SELECT 1"

# Check connection limit
psql -c "SELECT count(*) FROM pg_stat_activity;"

# Check pool configuration
env | grep DATABASE
```

**Solutions**:
- Verify network connectivity
- Check firewall rules
- Increase database connection limit
- Review connection pool settings

---

## Rollback Procedures

### Automated Rollback (Docker)

```bash
# Quick rollback to previous version
docker-compose down
docker image ls | grep aura
docker run -d --name aura-api registry.example.com/aura-backend:0.9.9
docker-compose up -d
```

### Database Rollback

```bash
# List available backups
aws s3 ls s3://aura-backups-prod/

# Restore from backup
aws s3 cp s3://aura-backups-prod/aura_backup_20260122_120000.sql.gz - | \
  gunzip | psql -h db.prod.internal -U aura_user -d aura_production
```

### DNS Rollback

```bash
# Update DNS to point to previous API endpoint
aws route53 change-resource-record-sets \
  --hosted-zone-id Z123ABC \
  --change-batch '{
    "Changes": [{
      "Action": "UPSERT",
      "ResourceRecordSet": {
        "Name": "api.aura.prod.internal",
        "Type": "A",
        "TTL": 60,
        "ResourceRecords": [{"Value": "203.0.113.10"}]
      }
    }]
  }'
```

### Communication Rollback

1. Notify all stakeholders immediately
2. Post status update to incident tracking system
3. Create post-mortem for analysis
4. Schedule team debrief

---

## Operational Runbook

### Daily Operations

**Morning Checklist**:
1. Check dashboard for alerts
2. Review error logs
3. Verify backup completion
4. Check capacity trends
5. Review performance metrics

**Weekly Tasks**:
1. Review slow query logs
2. Analyze usage patterns
3. Check security logs
4. Update runbooks
5. Team sync on issues

**Monthly Tasks**:
1. Capacity planning review
2. Cost optimization
3. Security audit
4. Disaster recovery drill
5. Performance optimization

### Escalation Contacts

```
On-call rotation:
- Primary: [Name] - [Phone] - [Email]
- Secondary: [Name] - [Phone] - [Email]
- Manager: [Name] - [Phone] - [Email]

Response SLAs:
- Critical: 15 minutes
- High: 1 hour
- Medium: 4 hours
- Low: 24 hours
```

---

## Success Criteria

✅ **Deployment is successful when**:
1. All services are running and healthy
2. Error rate is below 0.1%
3. P95 latency is below 1 second
4. All smoke tests pass
5. Database backups are working
6. Monitoring is collecting metrics
7. Team is confident in operations
8. No critical issues reported
9. Users report normal functionality
10. Performance meets or exceeds expectations

---

**Next Steps**:
1. Schedule deployment window
2. Notify stakeholders
3. Conduct final review
4. Execute deployment
5. Monitor closely for 72 hours
6. Conduct post-deployment review
7. Plan follow-up optimizations

**Questions?** Contact the AURA DevOps team at aura-devops@example.com

---

**Last Updated**: January 22, 2026  
**Version**: 1.0.0  
**Status**: ✅ APPROVED FOR PRODUCTION
