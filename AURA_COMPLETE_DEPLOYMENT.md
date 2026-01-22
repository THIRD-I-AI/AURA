# 🚀 AURA Complete Deployment & Validation Guide

## Overview

This guide walks you through deploying the complete AURA platform with all features:
- **Data Profiling & Semantic Modeling** (Steps 1-2)
- **Multi-Source Connectors** (Step 3)
- **SQL Safety & Validation** (Step 4)
- **Insights Generation** (Step 5)
- **Enhanced Scheduling** (Step 6)
- **Frontend UI Components** (Step 7)

## Prerequisites

- Python 3.9+
- Node.js 18+
- PostgreSQL 12+ (for testing)
- Google Cloud credentials (for BigQuery)
- All dependencies installed from `requirements.txt`

## 1. Backend Setup & Dependencies

### Install Python Dependencies

```bash
cd aurabackend
pip install -r requirements.txt
```

Key new dependencies added:
- `asyncpg` - PostgreSQL async driver
- `aiomysql` - MySQL async driver
- `google-cloud-bigquery` - BigQuery connector
- `pytest` & `pytest-asyncio` - Testing framework

### Initialize Metadata Database

```bash
python -c "
import asyncio
from aurabackend.metadata_store.db import init_db

asyncio.run(init_db())
print('✓ Metadata database initialized')
"
```

## 2. Start Backend Services

Start all services in separate terminals:

```bash
# Terminal 1: API Gateway (Enhanced)
cd aurabackend
python -m uvicorn api_gateway.enhanced_main:app --host 0.0.0.0 --port 8000 --reload

# Terminal 2: Database Service
python -m uvicorn database.main:app --host 0.0.0.0 --port 8002 --reload

# Terminal 3: Scheduler Service
python -m uvicorn scheduler_service.main:app --host 0.0.0.0 --port 8004 --reload

# Terminal 4: Orchestration Service
python -m uvicorn orchestration_service.main:app --host 0.0.0.0 --port 8001 --reload

# Terminal 5: Execution Sandbox
python -m uvicorn execution_sandbox.main:app --host 0.0.0.0 --port 8007 --reload
```

Or use the quick-start script:

```bash
./start-aura.ps1  # Windows PowerShell
```

### Verify Services Running

```bash
$ports = 8000, 8001, 8002, 8003, 8004, 8005, 8006, 8007
foreach ($port in $ports) {
    try {
        $result = Invoke-WebRequest -Uri "http://localhost:$port/health" -ErrorAction Stop -TimeoutSec 1
        Write-Host "✓ Port $port ready"
    } catch {
        Write-Host "✗ Port $port not responding"
    }
}
```

## 3. Frontend Setup

```bash
cd frontend
npm install
npm run build
npm run dev  # Dev server on http://localhost:5173
```

## 4. Run Integration Tests

```bash
cd aurabackend
python -m pytest tests/test_integration.py -v
```

### Test Categories

#### 4.1 File Profiling Tests
```bash
pytest tests/test_integration.py::test_file_service_profiling -v
```

#### 4.2 Semantic Modeling Tests
```bash
pytest tests/test_integration.py::test_semantic_model_generation -v
pytest tests/test_integration.py::test_semantic_modeling_pipeline -v
```

#### 4.3 SQL Safety Tests
```bash
pytest tests/test_integration.py::test_sql_validator_safe_query -v
pytest tests/test_integration.py::test_sql_validator_dangerous_query -v
pytest tests/test_integration.py::test_sql_validator_missing_limit -v
```

#### 4.4 Insights Tests
```bash
pytest tests/test_integration.py::test_insights_engine_analysis -v
pytest tests/test_integration.py::test_anomaly_detector -v
pytest tests/test_integration.py::test_alert_generator -v
```

#### 4.5 Connector Tests
```bash
pytest tests/test_integration.py -k connector -v
```

## 5. Validate Core Workflows

### 5.1 Upload & Profile Workflow

```bash
curl -X POST "http://localhost:8000/files/upload" \
  -F "file=@sample_data.csv" \
  -F "name=sample_data"

# Get file ID from response
FILE_ID="<returned_file_id>"

# Fetch stored profile
curl -X GET "http://localhost:8000/files/$FILE_ID/profile"
```

Expected response:
```json
{
  "file_id": "...",
  "rows": 1000,
  "columns": 5,
  "columns_profile": {
    "product_name": {
      "data_type": "string",
      "non_null": 1000,
      "distinct": 50,
      "samples": [...]
    },
    "revenue": {
      "data_type": "numeric",
      "non_null": 950,
      "min": 10.0,
      "max": 10000.0,
      "mean": 500.0
    }
  }
}
```

### 5.2 Semantic Model Auto-Generation

```bash
# Generate semantic model from profile
curl -X POST "http://localhost:8000/semantic/models/from-file/$FILE_ID" \
  -H "Content-Type: application/json"

# Retrieve generated model
curl -X GET "http://localhost:8000/semantic/models"
```

Expected model structure:
```json
{
  "id": "...",
  "name": "sample_data",
  "fields": [
    {
      "name": "product_name",
      "field_type": "dimension",
      "data_type": "string",
      "description": "Product identifier with 50 distinct values"
    },
    {
      "name": "revenue",
      "field_type": "measure",
      "data_type": "numeric",
      "aggregation": "sum",
      "description": "Revenue values ranging from 10.0 to 10000.0"
    }
  ]
}
```

### 5.3 Connector Testing

```bash
# Test PostgreSQL connection
curl -X POST "http://localhost:8000/connectors/postgresql/test" \
  -H "Content-Type: application/json" \
  -d '{
    "host": "localhost",
    "port": 5432,
    "username": "postgres",
    "password": "password",
    "database": "mydb"
  }'

# List tables
curl -X POST "http://localhost:8000/connectors/postgresql/tables" \
  -H "Content-Type: application/json" \
  -d '{
    "host": "localhost",
    "port": 5432,
    "username": "postgres",
    "password": "password",
    "database": "mydb"
  }'

# Profile a specific table
curl -X POST "http://localhost:8000/connectors/postgresql/profile" \
  -H "Content-Type: application/json" \
  -d '{
    "connector_type": "postgresql",
    "connector_config": {
      "host": "localhost",
      "port": 5432,
      "username": "postgres",
      "password": "password",
      "database": "mydb"
    },
    "table_name": "customers"
  }'
```

### 5.4 SQL Safety Validation

```bash
# Test query validation
curl -X POST "http://localhost:8000/validate/query" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "SELECT * FROM sales WHERE date >= \"2024-01-01\" LIMIT 100",
    "dry_run_mode": false,
    "max_rows": 10000
  }'

# Test dangerous query detection
curl -X POST "http://localhost:8000/validate/query" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "DELETE FROM sales; DROP TABLE users;",
    "dry_run_mode": false
  }'

# Lint query for optimizations
curl -X POST "http://localhost:8000/lint/query?query=SELECT * FROM sales" \
  -H "Content-Type: application/json"
```

### 5.5 Insights Generation

```bash
# Execute query with auto-insights
curl -X POST "http://localhost:8000/execute/query" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "SELECT date, product, revenue FROM sales LIMIT 1000",
    "connector_type": "postgresql",
    "connector_config": {
      "host": "localhost",
      "port": 5432,
      "username": "postgres",
      "password": "password",
      "database": "mydb"
    },
    "dry_run": false
  }'

# Expected insights response includes:
# - insights: [{ type, title, description, metric_value, confidence }]
# - charts: [{ type, title, data, config }]
# - narrative: Human-readable summary
```

## 6. Verify Frontend Components

### 6.1 Check Component Files

```bash
cd frontend/src/components

# New components should exist:
ls DataCatalog.*      # Data exploration UI
ls InsightsViewer.*   # Insights visualization
```

### 6.2 Test Components in App

Update `App.tsx` to include new components:

```tsx
import DataCatalog from './components/DataCatalog';
import InsightsViewer from './components/InsightsViewer';

// In mode switcher, add:
case 'catalog':
  return <DataCatalog />;
case 'insights':
  return <InsightsViewer />;
```

### 6.3 Verify in Browser

1. Open http://localhost:5173
2. Navigate through different modes
3. Test file upload → profiling → insights workflow

## 7. Complete End-to-End Test

```bash
# 1. Upload a CSV file via UI
# 2. View profile in /files/{id}/profile
# 3. Generate semantic model in /semantic/models/from-file/{id}
# 4. Test SQL query validation
# 5. Execute query and view auto-generated insights
# 6. Check alerts and anomalies
```

## 8. Production Deployment

### 8.1 Using Docker

```bash
# Build and run with Docker Compose
docker-compose up -d

# Verify containers
docker-compose ps

# View logs
docker-compose logs -f api_gateway
```

### 8.2 Environment Variables

Create `.env` file:

```env
# Database
DATABASE_URL=postgresql://user:password@localhost:5432/aura

# Services
API_GATEWAY_PORT=8000
SCHEDULER_SERVICE_URL=http://scheduler:8004
DATABASE_SERVICE_URL=http://database:8002

# Connectors
BIGQUERY_PROJECT_ID=my-project
BIGQUERY_CREDENTIALS=/secrets/bigquery.json

# Safety
MAX_QUERY_ROWS=100000
ALLOW_DDL_OPERATIONS=false
DRY_RUN_MODE=false

# Insights
ENABLE_ANOMALY_DETECTION=true
ANOMALY_DETECTION_THRESHOLD=2.0
GENERATE_NARRATIVES=true
```

### 8.3 Health Monitoring

```bash
# Monitor all services
watch -n 5 'curl -s http://localhost:8000/health | jq'

# Log aggregation
docker-compose logs --tail=100 -f

# Performance metrics
curl http://localhost:8000/metrics
```

## 9. Troubleshooting

### Issue: "Module not found" errors

```bash
# Ensure PYTHONPATH includes aurabackend
export PYTHONPATH="${PYTHONPATH}:$(pwd)/aurabackend"
```

### Issue: Database connection failures

```bash
# Check PostgreSQL is running
psql -U postgres -h localhost -c "SELECT 1"

# Initialize test database
createdb aura_test
```

### Issue: Frontend not updating

```bash
# Clear Vite cache and rebuild
cd frontend
rm -rf .vite
npm run build
npm run dev
```

### Issue: Connector timeouts

- Increase timeout values in connector config
- Check network connectivity to data sources
- Verify credentials and permissions

## 10. Performance Tuning

### Backend Optimization

```python
# Increase connection pool size for high concurrency
connector_config = ConnectorConfig(
    ...
    extra_params={"minsize": 10, "maxsize": 50}
)
```

### Query Performance

```bash
# Enable query profiling
curl -X POST "http://localhost:8000/validate/query" \
  -d '{"query": "...", "profile": true}'
```

### Frontend Optimization

```bash
# Build production bundle
cd frontend
npm run build

# Analyze bundle size
npm run build -- --report
```

## 11. Validation Checklist

- [ ] All backend services starting correctly
- [ ] Metadata database initialized
- [ ] File profiling working
- [ ] Semantic models auto-generated
- [ ] Connectors connecting to databases
- [ ] SQL queries validated for safety
- [ ] Insights generated from query results
- [ ] Frontend components rendering
- [ ] End-to-end workflow passing
- [ ] Integration tests passing (>95%)

## 12. Next Steps

1. **Productionize**: Deploy to cloud (AWS, GCP, Azure)
2. **Scale**: Implement caching, load balancing
3. **Monitor**: Add observability (logs, metrics, traces)
4. **Secure**: Implement auth, encryption, audit logging
5. **Extend**: Add custom connectors, plugins, models

## Support & Documentation

- **Architecture**: See `ARCHITECTURE.md`
- **API Docs**: http://localhost:8000/docs
- **Issues**: Check service logs and health endpoints
- **Contributing**: Follow development guidelines in README

---

**AURA is now ready for enterprise data analytics!** 🚀
