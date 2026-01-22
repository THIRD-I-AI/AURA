# AURA Operations Runbook

**Last Updated**: January 22, 2026  
**Version**: 1.0.0  
**Audience**: Operations Team, On-Call Engineers, DevOps

---

## Quick Reference

### Service Status Check

```bash
# All services
for port in 8000 8001 8002 8004 8007; do
  echo "Port $port: $(curl -s http://localhost:$port/health | jq -r .status)"
done

# Or
docker-compose ps
```

### Common Commands

```bash
# View logs
docker-compose logs -f aura-api          # API logs
docker-compose logs -f aura-db           # Database logs
tail -f /var/log/aura/aura.log           # Application logs

# Restart service
docker-compose restart aura-api

# Scale API instances
docker-compose up -d --scale aura-api=3

# Database backup
pg_dump -h db.prod.internal -U aura_user aura_production > backup.sql.gz

# Clear cache
redis-cli -h cache.prod.internal FLUSHALL
```

---

## Incident Response

### Service Down

**Priority**: CRITICAL

**Steps**:
1. Verify service is actually down:
   ```bash
   curl -v http://localhost:8000/health
   ```

2. Check logs for errors:
   ```bash
   docker-compose logs aura-api | tail -100
   ```

3. Restart the service:
   ```bash
   docker-compose restart aura-api
   ```

4. Verify recovery:
   ```bash
   curl http://localhost:8000/health
   ```

5. If still down, check dependencies:
   ```bash
   # PostgreSQL
   psql -h db.prod.internal -c "SELECT 1"
   
   # Redis
   redis-cli -h cache.prod.internal PING
   ```

6. If dependencies are down, contact infrastructure team

7. If service still won't start, escalate to on-call engineer

---

### High Error Rate (> 1% errors)

**Priority**: HIGH

**Steps**:
1. Check recent error logs:
   ```bash
   docker-compose logs aura-api | grep ERROR | tail -50
   ```

2. Check for specific error patterns:
   ```bash
   curl http://localhost:8000/metrics | grep http_requests_total
   ```

3. Potential causes:
   - Database connectivity issue → Check database
   - Missing configuration → Check .env file
   - Recent code change → Check git log
   - Resource exhaustion → Check CPU/memory
   - External service down → Check dependencies

4. Possible solutions:
   - Restart service (50% of issues)
   - Clear cache
   - Scale horizontally
   - Rollback recent changes
   - Contact on-call engineer

---

### High Latency (P95 > 1 second)

**Priority**: HIGH

**Steps**:
1. Check which endpoint is slow:
   ```bash
   curl http://localhost:8000/metrics | grep http_request_duration_seconds
   ```

2. Most common causes:
   - Slow database queries → Check slow query log
   - Memory pressure → Check available memory
   - Disk I/O contention → Check disk usage
   - Network issues → Check connectivity
   - Cache misses → Check Redis connectivity

3. Quick fixes:
   ```bash
   # Clear slow query cache
   psql -c "SELECT pg_stat_statements_reset();"
   
   # Clear Redis cache
   redis-cli FLUSHDB
   
   # Restart service
   docker-compose restart aura-api
   ```

4. If issue persists:
   - Scale to additional instances
   - Optimize slow queries (contact DBA)
   - Add caching layer
   - Contact on-call engineer

---

### Database Connection Errors

**Priority**: CRITICAL

**Symptoms**:
- "Connection refused"
- "Connection pool exhausted"
- "Too many connections"

**Steps**:
1. Check PostgreSQL is running:
   ```bash
   psql -h db.prod.internal -c "SELECT 1"
   ```

2. Check current connections:
   ```bash
   psql -c "SELECT count(*) FROM pg_stat_activity;"
   ```

3. If > 90% of max_connections:
   ```bash
   # Kill idle connections
   psql -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity 
            WHERE state = 'idle' AND query_start < now() - INTERVAL '10 min';"
   ```

4. Check connection pool configuration:
   ```bash
   echo $DATABASE_POOL_SIZE
   ```

5. If database is down:
   - Contact infrastructure team
   - Activate failover if configured
   - Escalate to on-call engineer

---

### Disk Space Low

**Priority**: MEDIUM

**Symptoms**:
- File uploads failing
- Application won't start
- Database inserts failing

**Steps**:
1. Check disk usage:
   ```bash
   df -h /var/aura
   ```

2. Find largest files:
   ```bash
   du -sh /var/aura/* | sort -rh | head -10
   ```

3. Quick cleanup:
   ```bash
   # Remove old upload files (older than 30 days)
   find /var/aura/uploads -type f -mtime +30 -delete
   
   # Clear temp directory
   rm -rf /var/aura/temp/*
   
   # Clear Docker cache
   docker system prune -a --volumes
   ```

4. If still low:
   - Archive old data to S3
   - Expand storage volume
   - Contact infrastructure team

---

### Memory Leak

**Priority**: HIGH

**Symptoms**:
- Memory usage continuously increasing
- Application becomes slower over time
- Eventually runs out of memory

**Steps**:
1. Monitor memory:
   ```bash
   docker stats aura-api --no-stream
   ```

2. Check for specific process:
   ```bash
   ps aux | grep python | grep aura
   ```

3. Enable memory profiling:
   ```bash
   export MEMORY_PROFILE=true
   docker-compose restart aura-api
   ```

4. Wait 1 hour, then collect profile:
   ```bash
   docker-compose exec aura-api python -m memory_profiler
   ```

5. Temporary fix:
   - Restart service regularly (hourly restart job)
   
6. Permanent fix:
   - Contact development team
   - Debug memory usage
   - Deploy fix in next release

---

## Maintenance Tasks

### Daily (Automated)

- [ ] Database backup
- [ ] Monitor metrics
- [ ] Check alerting system
- [ ] Verify replicas are healthy

### Weekly

- [ ] Review slow query logs
- [ ] Analyze error patterns
- [ ] Check certificate expiry (90+ days)
- [ ] Update monitoring dashboards

### Monthly

- [ ] Disaster recovery drill
- [ ] Capacity planning review
- [ ] Security audit
- [ ] Performance optimization review
- [ ] Team training

### Quarterly

- [ ] Major version upgrades
- [ ] Dependency updates
- [ ] Architecture review
- [ ] Cost analysis

---

## Performance Tuning

### Optimize Database Queries

```sql
-- Find slow queries
SELECT query, calls, mean_time 
FROM pg_stat_statements 
WHERE mean_time > 100 
ORDER BY mean_time DESC;

-- Create missing indexes
CREATE INDEX idx_files_created_at ON files(created_at);
CREATE INDEX idx_profiles_dataset_id ON dataset_profiles(dataset_name);

-- Analyze table statistics
ANALYZE files;
VACUUM ANALYZE dataset_profiles;
```

### Scale Horizontally

```bash
# Add more API instances
docker-compose up -d --scale aura-api=5

# Or in Kubernetes
kubectl scale deployment/aura-api --replicas=5
```

### Increase Cache TTL

```bash
# In .env.production
CACHE_TTL=3600  # 1 hour

# Then restart
docker-compose restart aura-api
```

### Enable Query Result Caching

```bash
# Configuration
CACHE_ENABLED=true
CACHE_DB_QUERIES=true
CACHE_TTL=300  # 5 minutes
```

---

## Backup & Recovery

### Automated Daily Backup

```bash
# Runs at 2 AM UTC
# Creates: aura_backup_YYYYMMDD_HHMMSS.sql.gz
# Uploads to: s3://aura-backups-prod/
```

### Manual Backup

```bash
# Create backup
pg_dump -h db.prod.internal -U aura_user aura_production | \
  gzip > aura_backup_$(date +%Y%m%d_%H%M%S).sql.gz

# Upload to S3
aws s3 cp aura_backup_20260122_143000.sql.gz s3://aura-backups-prod/
```

### Restore from Backup

```bash
# List available backups
aws s3 ls s3://aura-backups-prod/

# Download backup
aws s3 cp s3://aura-backups-prod/aura_backup_20260122_120000.sql.gz .

# Restore
gunzip -c aura_backup_20260122_120000.sql.gz | \
  psql -h db.prod.internal -U aura_user aura_production

# Verify
psql -h db.prod.internal -U aura_user aura_production -c "SELECT count(*) FROM files;"
```

---

## Security

### Access Control

```bash
# SSH key management
# Only authorized keys in ~/.ssh/authorized_keys

# Database credentials
# Stored in AWS Secrets Manager
# Rotated every 90 days

# API tokens
# JWT tokens expire after 1 hour
# Refresh tokens expire after 30 days
```

### Secret Management

```bash
# View secrets
aws secretsmanager get-secret-value --secret-id aura-prod-secrets

# Rotate secrets
aws secretsmanager rotate-secret --secret-id aura-prod-secrets

# Update .env
source <(aws secretsmanager get-secret-value --secret-id aura-prod-secrets --query SecretString --output text)
```

### SSL/TLS Certificates

```bash
# Check certificate expiry
openssl s_client -connect api.aura.prod.internal:443 -showcerts | \
  openssl x509 -dates -noout

# Renew certificate (Let's Encrypt)
certbot renew --force-renewal

# Verify
curl -I https://api.aura.prod.internal
```

---

## Monitoring Queries

### API Health

```bash
# Request rate
curl http://localhost:8000/metrics | grep http_requests_total

# Error rate
curl http://localhost:8000/metrics | grep http_requests_total | grep status | grep "5"

# Latency (P95)
curl http://localhost:8000/metrics | grep http_request_duration_seconds_bucket | grep le=\"1\"
```

### Database Health

```sql
-- Current connections
SELECT count(*) FROM pg_stat_activity;

-- Table sizes
SELECT schemaname, tablename, pg_size_pretty(pg_total_relation_size(schemaname||'.'||tablename))
FROM pg_tables ORDER BY pg_total_relation_size(schemaname||'.'||tablename) DESC;

-- Index usage
SELECT schemaname, tablename, indexname, idx_scan
FROM pg_stat_user_indexes ORDER BY idx_scan DESC;

-- Missing indexes
SELECT t1.relname, t2.relname, idx_scan
FROM pg_stat_user_indexes
JOIN pg_index ON pg_index.indexrelid = pg_stat_user_indexes.indexrelid
JOIN pg_class t1 ON pg_index.indrelid = t1.oid
JOIN pg_class t2 ON pg_index.indexrelid = t2.oid
WHERE idx_scan = 0 AND idx_blks_read > 0;
```

### Redis Health

```bash
# Server info
redis-cli INFO server

# Memory usage
redis-cli INFO memory

# Key count
redis-cli DBSIZE

# Slow commands
redis-cli CONFIG GET slowlog-log-slower-than
redis-cli SLOWLOG GET 10
```

---

## Troubleshooting Tools

### Tcpdump (Network)

```bash
# Capture API traffic
tcpdump -i any -n port 8000 -A | head -100

# Capture database traffic
tcpdump -i any -n port 5432 -A | head -100
```

### Strace (System Calls)

```bash
# Trace API process
strace -p $(pidof python) -f -e trace=network,open,read,write

# Trace database operations
strace -p $(pidof postgres) -f
```

### Perf (Performance Profiling)

```bash
# Profile API
perf record -p $(pidof python) -g -- sleep 60
perf report

# Flamegraph
perf script | stackcollapse-perf.pl | flamegraph.pl > flamegraph.svg
```

---

## Escalation

### On-Call Engineer Contact

**During Business Hours**:
- Slack: #aura-incidents
- Email: aura-team@example.com
- Phone: [Primary phone]

**After Hours**:
- Phone: [On-call rotation number]
- PagerDuty: [Integration]
- Slack: @on-call

### SLAs

| Severity | Response | Resolution |
|----------|----------|------------|
| Critical | 15 min   | 4 hours    |
| High     | 1 hour   | 24 hours   |
| Medium   | 4 hours  | 72 hours   |
| Low      | 24 hours | 1 week     |

---

**Document Version**: 1.0.0  
**Last Reviewed**: January 22, 2026  
**Next Review**: April 22, 2026
