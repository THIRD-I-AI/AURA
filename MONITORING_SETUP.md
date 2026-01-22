# AURA Monitoring & Observability Setup Guide

**Last Updated**: January 22, 2026  
**Version**: 1.0.0  
**Purpose**: Complete monitoring configuration for production AURA deployment

---

## Overview

This guide covers:
- **Metrics Collection**: Prometheus scraping configuration
- **Dashboards**: Grafana visualizations
- **Alerting**: Alert rules and notifications
- **Log Aggregation**: Centralized log management
- **Distributed Tracing**: Request tracing across services
- **Health Checks**: Service health verification

---

## Part 1: Prometheus Setup

### Installation

```bash
# Docker
docker pull prom/prometheus:latest

# Or with docker-compose
docker-compose pull prometheus
```

### Configuration File: `prometheus.yml`

```yaml
global:
  scrape_interval: 15s
  evaluation_interval: 15s
  external_labels:
    environment: 'production'
    team: 'aura'

alerting:
  alertmanagers:
    - static_configs:
        - targets:
            - localhost:9093

rule_files:
  - '/etc/prometheus/alert_rules.yml'

scrape_configs:
  # AURA API Gateway
  - job_name: 'aura-api'
    static_configs:
      - targets: ['localhost:8000']
    relabel_configs:
      - source_labels: [__address__]
        target_label: instance
    scrape_interval: 10s

  # AURA Database Service
  - job_name: 'aura-database'
    static_configs:
      - targets: ['localhost:8002']
    relabel_configs:
      - source_labels: [__address__]
        target_label: instance

  # PostgreSQL (via postgres_exporter)
  - job_name: 'postgresql'
    static_configs:
      - targets: ['localhost:9187']
    relabel_configs:
      - source_labels: [__address__]
        target_label: instance

  # Redis (via redis_exporter)
  - job_name: 'redis'
    static_configs:
      - targets: ['localhost:9121']
    relabel_configs:
      - source_labels: [__address__]
        target_label: instance

  # Node Exporter (System Metrics)
  - job_name: 'node'
    static_configs:
      - targets: ['localhost:9100']
    relabel_configs:
      - source_labels: [__address__]
        target_label: instance

  # Docker
  - job_name: 'docker'
    static_configs:
      - targets: ['localhost:9323']
    relabel_configs:
      - source_labels: [__address__]
        target_label: instance
```

### Running Prometheus

```bash
# With docker-compose
docker-compose up -d prometheus

# Verify
curl http://localhost:9090

# Access web UI
# http://localhost:9090
```

---

## Part 2: Grafana Setup

### Installation

```bash
# Docker
docker pull grafana/grafana:latest

# docker-compose (already configured)
docker-compose up -d grafana
```

### Initial Login

```
URL: http://localhost:3000
Username: admin
Password: admin (change on first login)
```

### Add Prometheus Data Source

1. Settings → Data Sources
2. Click "Add data source"
3. Select "Prometheus"
4. Configure:
   ```
   URL: http://prometheus:9090
   Access: Server (default)
   ```
5. Click "Test & Save"

### Dashboard Configuration

#### Dashboard 1: System Overview

```json
{
  "dashboard": {
    "title": "AURA System Overview",
    "panels": [
      {
        "title": "API Request Rate",
        "targets": [
          {
            "expr": "rate(http_requests_total[5m])"
          }
        ],
        "type": "graph"
      },
      {
        "title": "Error Rate",
        "targets": [
          {
            "expr": "rate(http_requests_total{status=~'5..'}[5m])"
          }
        ],
        "type": "graph"
      },
      {
        "title": "API Latency (P95)",
        "targets": [
          {
            "expr": "histogram_quantile(0.95, rate(http_request_duration_seconds_bucket[5m]))"
          }
        ],
        "type": "graph"
      },
      {
        "title": "Database Connections",
        "targets": [
          {
            "expr": "pg_stat_activity_count"
          }
        ],
        "type": "gauge"
      }
    ]
  }
}
```

#### Dashboard 2: API Performance

```json
{
  "dashboard": {
    "title": "AURA API Performance",
    "panels": [
      {
        "title": "Request Distribution by Endpoint",
        "targets": [
          {
            "expr": "sum by (path) (rate(http_requests_total[5m]))"
          }
        ],
        "type": "bargauge"
      },
      {
        "title": "Latency by Endpoint",
        "targets": [
          {
            "expr": "histogram_quantile(0.95, sum by (path, le) (rate(http_request_duration_seconds_bucket[5m])))"
          }
        ],
        "type": "table"
      },
      {
        "title": "Error Rate by Endpoint",
        "targets": [
          {
            "expr": "sum by (path) (rate(http_requests_total{status=~'[45]..'}[5m]))"
          }
        ],
        "type": "graph"
      },
      {
        "title": "Active Requests",
        "targets": [
          {
            "expr": "http_requests_in_progress"
          }
        ],
        "type": "gauge"
      }
    ]
  }
}
```

#### Dashboard 3: Database Health

```json
{
  "dashboard": {
    "title": "AURA Database Health",
    "panels": [
      {
        "title": "Active Connections",
        "targets": [
          {
            "expr": "pg_stat_activity_count"
          }
        ],
        "type": "gauge"
      },
      {
        "title": "Connection Pool Usage",
        "targets": [
          {
            "expr": "pg_stat_activity_count / 20 * 100"
          }
        ],
        "type": "gauge"
      },
      {
        "title": "Query Performance",
        "targets": [
          {
            "expr": "pg_stat_statements_mean_time"
          }
        ],
        "type": "graph"
      },
      {
        "title": "Slow Queries (>100ms)",
        "targets": [
          {
            "expr": "pg_stat_statements_mean_time > 100"
          }
        ],
        "type": "table"
      },
      {
        "title": "Database Size",
        "targets": [
          {
            "expr": "pg_database_size_bytes"
          }
        ],
        "type": "gauge"
      },
      {
        "title": "Cache Hit Ratio",
        "targets": [
          {
            "expr": "rate(pg_stat_database_blks_hit[5m]) / (rate(pg_stat_database_blks_hit[5m]) + rate(pg_stat_database_blks_read[5m]))"
          }
        ],
        "type": "gauge"
      }
    ]
  }
}
```

---

## Part 3: Alert Rules

### File: `alert_rules.yml`

```yaml
groups:
  - name: aura_alerts
    interval: 30s
    rules:
      # API Alerts
      - alert: HighErrorRate
        expr: rate(http_requests_total{status=~"5.."}[5m]) > 0.05
        for: 5m
        labels:
          severity: critical
          component: api
        annotations:
          summary: "High error rate detected"
          description: "Error rate is {{ $value | humanizePercentage }} for instance {{ $labels.instance }}"

      - alert: HighLatency
        expr: histogram_quantile(0.95, rate(http_request_duration_seconds_bucket[5m])) > 1.0
        for: 5m
        labels:
          severity: high
          component: api
        annotations:
          summary: "High API latency detected"
          description: "P95 latency is {{ $value | humanizeDuration }} for instance {{ $labels.instance }}"

      - alert: LowThroughput
        expr: rate(http_requests_total[5m]) < 1
        for: 10m
        labels:
          severity: high
          component: api
        annotations:
          summary: "Low request throughput"
          description: "Request rate is {{ $value }} req/s for instance {{ $labels.instance }}"

      # Database Alerts
      - alert: HighConnectionUsage
        expr: pg_stat_activity_count / 20 > 0.8
        for: 5m
        labels:
          severity: high
          component: database
        annotations:
          summary: "High database connection usage"
          description: "Connection pool usage is {{ $value | humanizePercentage }} on {{ $labels.instance }}"

      - alert: SlowQueries
        expr: pg_stat_statements_mean_time > 500
        for: 5m
        labels:
          severity: medium
          component: database
        annotations:
          summary: "Slow queries detected"
          description: "Average query time is {{ $value }}ms on {{ $labels.instance }}"

      - alert: HighDiskUsage
        expr: (node_filesystem_avail_bytes / node_filesystem_size_bytes) < 0.1
        for: 10m
        labels:
          severity: high
          component: system
        annotations:
          summary: "Disk space running low"
          description: "Available disk space is {{ $value | humanizePercentage }} on {{ $labels.instance }}"

      - alert: HighMemoryUsage
        expr: (1 - (node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes)) > 0.9
        for: 5m
        labels:
          severity: high
          component: system
        annotations:
          summary: "High memory usage detected"
          description: "Memory usage is {{ $value | humanizePercentage }} on {{ $labels.instance }}"

      # Redis Alerts
      - alert: RedisDown
        expr: up{job="redis"} == 0
        for: 1m
        labels:
          severity: critical
          component: cache
        annotations:
          summary: "Redis is down"
          description: "Redis has been unreachable for 1 minute on {{ $labels.instance }}"

      - alert: HighRedisMem
        expr: redis_memory_used_bytes / redis_memory_max_bytes > 0.85
        for: 5m
        labels:
          severity: high
          component: cache
        annotations:
          summary: "Redis memory usage high"
          description: "Redis memory usage is {{ $value | humanizePercentage }} on {{ $labels.instance }}"

      # Service Alerts
      - alert: ServiceDown
        expr: up == 0
        for: 2m
        labels:
          severity: critical
          component: "{{ $labels.job }}"
        annotations:
          summary: "Service {{ $labels.job }} is down"
          description: "{{ $labels.job }} on {{ $labels.instance }} has been unreachable for 2 minutes"

      # PostgreSQL Replication
      - alert: ReplicationLag
        expr: pg_replication_lag_seconds > 30
        for: 5m
        labels:
          severity: high
          component: database
        annotations:
          summary: "Database replication lag detected"
          description: "Replication lag is {{ $value }}s on {{ $labels.instance }}"

      - alert: ReplicationSlotFull
        expr: pg_replication_slot_files > 1000000
        for: 10m
        labels:
          severity: high
          component: database
        annotations:
          summary: "Replication slot may be full"
          description: "Replication slot has {{ $value }} files on {{ $labels.instance }}"
```

### Alert Manager Configuration

```yaml
# alertmanager.yml
global:
  resolve_timeout: 5m

route:
  receiver: 'default'
  group_by: ['alertname', 'cluster', 'service']
  group_wait: 30s
  group_interval: 5m
  repeat_interval: 4h

  routes:
    - match:
        severity: critical
      receiver: 'pagerduty'
      group_wait: 10s
      
    - match:
        severity: high
      receiver: 'slack'
      group_wait: 5m

receivers:
  - name: 'default'
    slack_configs:
      - api_url: 'YOUR_SLACK_WEBHOOK_URL'
        channel: '#aura-alerts'
        title: 'AURA Alert'
        text: '{{ range .Alerts }}{{ .Annotations.description }}{{ end }}'

  - name: 'slack'
    slack_configs:
      - api_url: 'YOUR_SLACK_WEBHOOK_URL'
        channel: '#aura-incidents'
        title: '[{{ .GroupLabels.severity }}] {{ .GroupLabels.alertname }}'

  - name: 'pagerduty'
    pagerduty_configs:
      - service_key: 'YOUR_PAGERDUTY_SERVICE_KEY'
        description: '{{ .GroupLabels.alertname }}'
```

---

## Part 4: Logging Setup

### ELK Stack Configuration

```yaml
# docker-compose addition for logging
elasticsearch:
  image: docker.elastic.co/elasticsearch/elasticsearch:8.0.0
  environment:
    - discovery.type=single-node
    - xpack.security.enabled=false
  ports:
    - "9200:9200"
  volumes:
    - elasticsearch-data:/usr/share/elasticsearch/data

kibana:
  image: docker.elastic.co/kibana/kibana:8.0.0
  ports:
    - "5601:5601"
  environment:
    - ELASTICSEARCH_HOSTS=http://elasticsearch:9200
  depends_on:
    - elasticsearch

filebeat:
  image: docker.elastic.co/beats/filebeat:8.0.0
  user: root
  volumes:
    - /var/lib/docker/containers:/var/lib/docker/containers:ro
    - /var/run/docker.sock:/var/run/docker.sock:ro
    - ./filebeat.yml:/usr/share/filebeat/filebeat.yml:ro
  command: filebeat -e -strict.perms=false
  depends_on:
    - elasticsearch

volumes:
  elasticsearch-data:
```

### Filebeat Configuration

```yaml
# filebeat.yml
filebeat.inputs:
  - type: docker
    enabled: true
    containers.ids:
      - '*'

processors:
  - add_docker_metadata:
      host: "unix:///var/run/docker.sock"
  - add_kubernetes_metadata:
  - decode_json_fields:
      fields: ["message"]
      target: "json"

output.elasticsearch:
  hosts: ["elasticsearch:9200"]
  index: "aura-%{+yyyy.MM.dd}"

logging.level: info
```

### Kibana Index Pattern

1. Go to **Stack Management** → **Index Patterns**
2. Create new pattern: `aura-*`
3. Time field: `@timestamp`
4. Create index pattern

### Common Kibana Queries

```json
// Error logs
{
  "query": {
    "match": {
      "log.level": "ERROR"
    }
  }
}

// Slow requests
{
  "query": {
    "range": {
      "http.request.duration_ms": {
        "gte": 1000
      }
    }
  }
}

// Specific service
{
  "query": {
    "match": {
      "service.name": "aura-api"
    }
  }
}
```

---

## Part 5: Distributed Tracing

### Jaeger Setup

```yaml
# docker-compose
jaeger:
  image: jaegertracing/all-in-one:latest
  ports:
    - "6831:6831/udp"  # Jaeger agent
    - "16686:16686"    # Jaeger UI
  environment:
    - COLLECTOR_ZIPKIN_HTTP_PORT=9411
```

### Instrumentation

```python
# In aurabackend/api_gateway/main.py
from jaeger_client import Config
from opentelemetry import trace, metrics
from opentelemetry.exporter.jaeger.thrift import JaegerExporter
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor

# Configure Jaeger
jaeger_exporter = JaegerExporter(
    agent_host_name="localhost",
    agent_port=6831,
)

trace.set_tracer_provider(TracerProvider())
trace.get_tracer_provider().add_span_processor(
    SimpleSpanProcessor(jaeger_exporter)
)

tracer = trace.get_tracer(__name__)

# Use in endpoints
@app.post("/api/files/upload")
async def upload_file(file: UploadFile):
    with tracer.start_as_current_span("file_upload"):
        # Your code here
        pass
```

### Accessing Jaeger

```
URL: http://localhost:16686
```

---

## Part 6: Health Checks

### Health Check Endpoints

```python
# aurabackend/api_gateway/main.py
@app.get("/health")
async def health():
    return {
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat(),
        "services": {
            "database": check_database(),
            "redis": check_redis(),
            "file_service": check_file_service(),
        }
    }

def check_database():
    try:
        # Test connection
        return {"status": "up", "latency_ms": 5}
    except:
        return {"status": "down", "latency_ms": None}

def check_redis():
    try:
        # Test PING
        return {"status": "up", "latency_ms": 2}
    except:
        return {"status": "down", "latency_ms": None}
```

### Kubernetes Liveness/Readiness Probes

```yaml
# If using Kubernetes
apiVersion: apps/v1
kind: Deployment
metadata:
  name: aura-api
spec:
  template:
    spec:
      containers:
      - name: api
        image: aura-backend:latest
        
        livenessProbe:
          httpGet:
            path: /health
            port: 8000
          initialDelaySeconds: 30
          periodSeconds: 10
          timeoutSeconds: 5
          failureThreshold: 3
        
        readinessProbe:
          httpGet:
            path: /health
            port: 8000
          initialDelaySeconds: 20
          periodSeconds: 5
          timeoutSeconds: 3
          failureThreshold: 2
```

---

## Monitoring Dashboard Checklist

- [ ] Prometheus running and scraping metrics
- [ ] Grafana connected to Prometheus
- [ ] System Overview dashboard created
- [ ] API Performance dashboard created
- [ ] Database Health dashboard created
- [ ] Alert rules imported
- [ ] AlertManager configured
- [ ] Slack integration enabled
- [ ] PagerDuty integration (if applicable)
- [ ] Elasticsearch and Kibana running
- [ ] Filebeat collecting logs
- [ ] Jaeger running and receiving traces
- [ ] Health check endpoint accessible
- [ ] All services reporting in Prometheus
- [ ] Sample alerts verified

---

## Accessing Services

| Service | URL | Default Creds |
|---------|-----|---------------|
| Prometheus | http://localhost:9090 | None |
| Grafana | http://localhost:3000 | admin/admin |
| AlertManager | http://localhost:9093 | None |
| Kibana | http://localhost:5601 | None |
| Jaeger | http://localhost:16686 | None |

---

## Performance Baselines

| Metric | Target | Alert Threshold |
|--------|--------|-----------------|
| API Error Rate | < 0.1% | > 1% |
| P95 Latency | < 500ms | > 1000ms |
| DB Connections | < 10 | > 18 (90%) |
| CPU Usage | < 70% | > 85% |
| Memory Usage | < 70% | > 85% |
| Disk Usage | < 70% | > 85% |
| Cache Hit Ratio | > 80% | < 60% |

---

**Document Version**: 1.0.0  
**Last Reviewed**: January 22, 2026  
**Next Review**: April 22, 2026
