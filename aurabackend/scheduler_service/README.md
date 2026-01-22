# AURA Scheduler Service

Automated job scheduling and execution service for running database queries on a schedule.

## Features

- **Flexible Scheduling**: Support for multiple schedule types:
  - One-time execution
  - Hourly, Daily, Weekly, Monthly schedules
  - Cron expressions (for advanced scheduling)
  
- **Robust Execution**: 
  - Automatic retry logic with configurable delays
  - Timeout management
  - Error tracking and logging
  
- **Execution Tracking**:
  - Complete execution history
  - Detailed logs for each execution
  - Status tracking (pending, running, success, failed)
  
- **Result Management**:
  - Optional result storage
  - Row count tracking
  - Sample data storage for verification

## Architecture

```
scheduler_service/
├── models.py          # SQLAlchemy models (ScheduledJob, JobExecution, ExecutionLog)
├── repository.py      # Data access layer with async SQLAlchemy
├── executor.py        # Job execution engine with retry logic
├── worker.py          # Background worker that checks for jobs
└── main.py            # FastAPI REST API
```

## Database Schema

### ScheduledJob
- Job configuration and schedule definition
- Fields: name, description, connection_id, query, schedule_type, schedule_config, timeout, retries, is_active
- Tracks last and next execution times

### JobExecution
- Records each job execution attempt
- Fields: status, started_at, completed_at, duration, rows_affected, error_message, retry_count
- Links to job and stores result summaries

### ExecutionLog
- Detailed logging for each execution
- Fields: timestamp, level (INFO/ERROR/WARNING), message, details
- Links to execution for troubleshooting

## API Endpoints

### Job Management
- `POST /jobs` - Create a new scheduled job
- `GET /jobs` - List all jobs (optionally filter by active status)
- `GET /jobs/{id}` - Get job details
- `PUT /jobs/{id}` - Update a job
- `DELETE /jobs/{id}` - Delete a job
- `POST /jobs/{id}/pause` - Pause a job
- `POST /jobs/{id}/resume` - Resume a paused job
- `POST /jobs/{id}/execute` - Manually trigger job execution

### Execution History
- `GET /executions` - List executions (filter by job_id, status)
- `GET /executions/{id}` - Get execution details
- `GET /executions/{id}/logs` - Get execution logs

### Admin
- `POST /admin/cleanup?retention_days=30` - Clean up old execution records

## Usage Examples

### Create a Daily Job

```bash
curl -X POST http://localhost:8004/jobs \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Daily Sales Report",
    "description": "Generate daily sales summary",
    "connection_id": "your-connection-id",
    "query": "SELECT date, SUM(amount) FROM sales WHERE date = CURRENT_DATE GROUP BY date",
    "schedule_type": "daily",
    "schedule_config": {
      "hour": 9,
      "minute": 0
    },
    "timeout_seconds": 300,
    "max_retries": 3,
    "retry_delay_seconds": 60,
    "store_results": true,
    "is_active": true
  }'
```

### Create an Hourly Job

```bash
curl -X POST http://localhost:8004/jobs \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Hourly Data Sync",
    "description": "Sync data every hour",
    "connection_id": "your-connection-id",
    "query": "INSERT INTO staging SELECT * FROM source WHERE updated_at > NOW() - INTERVAL 1 HOUR",
    "schedule_type": "hourly",
    "timeout_seconds": 600,
    "max_retries": 5,
    "is_active": true
  }'
```

### Create a Weekly Job

```bash
curl -X POST http://localhost:8004/jobs \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Weekly Backup",
    "description": "Weekly data backup",
    "connection_id": "your-connection-id",
    "query": "CALL backup_procedure()",
    "schedule_type": "weekly",
    "schedule_config": {
      "day_of_week": 0,
      "hour": 2,
      "minute": 0
    },
    "timeout_seconds": 1800,
    "is_active": true
  }'
```

### List All Active Jobs

```bash
curl http://localhost:8004/jobs?is_active=true
```

### Get Execution History for a Job

```bash
curl http://localhost:8004/executions?job_id=your-job-id
```

### Manually Trigger a Job

```bash
curl -X POST http://localhost:8004/jobs/your-job-id/execute
```

### View Execution Logs

```bash
curl http://localhost:8004/executions/your-execution-id/logs
```

## Running the Service

### Standalone

```bash
# Set environment variables
export SCHEDULER_DATABASE_URL="sqlite+aiosqlite:///data/scheduler.db"
export DATABASE_SERVICE_URL="http://localhost:8002"
export SCHEDULER_PORT="8004"
export SCHEDULER_CHECK_INTERVAL="60"

# Run the service
cd aurabackend/scheduler_service
python -m uvicorn main:app --host 0.0.0.0 --port 8004 --reload
```

### Using PowerShell Script

```powershell
.\start-scheduler.ps1
```

### With Docker Compose

```bash
docker-compose up scheduler_service
```

## Configuration

Environment variables:

- `SCHEDULER_DATABASE_URL` - SQLAlchemy database URL (default: `sqlite+aiosqlite:///data/scheduler.db`)
- `DATABASE_SERVICE_URL` - URL of the database service for query execution (default: `http://localhost:8002`)
- `SCHEDULER_PORT` - Port to run the service on (default: `8004`)
- `SCHEDULER_CHECK_INTERVAL` - How often to check for jobs in seconds (default: `60`)

## Schedule Types

### One-Time (`once`)
Execute job once, then deactivate.

### Hourly (`hourly`)
Execute every hour.

### Daily (`daily`)
Execute at a specific time each day.

**schedule_config**:
```json
{
  "hour": 9,    // 0-23
  "minute": 0   // 0-59
}
```

### Weekly (`weekly`)
Execute on a specific day of the week.

**schedule_config**:
```json
{
  "day_of_week": 0,  // 0=Monday, 6=Sunday
  "hour": 9,
  "minute": 0
}
```

### Monthly (`monthly`)
Execute on a specific day of the month.

**schedule_config**:
```json
{
  "day": 1,     // 1-31
  "hour": 9,
  "minute": 0
}
```

### Cron Expression (`cron`)
Advanced scheduling using cron expressions (requires cron parser library).

## Retry Logic

When a job execution fails:
1. Error is logged to execution record
2. If `retry_count < max_retries`, job is retried after `retry_delay_seconds`
3. If max retries exceeded, job status is set to `FAILED`
4. Next scheduled execution proceeds normally (failures don't affect schedule)

## Monitoring

### Check Service Health

```bash
curl http://localhost:8004/
```

Response:
```json
{
  "service": "scheduler",
  "status": "running",
  "worker_active": true
}
```

### View Recent Executions

```bash
curl http://localhost:8004/executions?limit=10
```

### Filter Failed Executions

```bash
curl http://localhost:8004/executions?status=FAILED
```

## Maintenance

### Clean Up Old Records

```bash
curl -X POST http://localhost:8004/admin/cleanup?retention_days=30
```

This removes execution logs and execution records older than 30 days.

## Integration

The scheduler service integrates with:
- **Database Service** (port 8002): Executes queries via `/connections/{id}/query` endpoint
- **Frontend** (to be implemented): Pipelines tab for UI management
- **Metadata Store**: Can store result summaries and execution metadata

## Security Notes

- Jobs inherit connection credentials from the database service
- Ensure proper access control on the scheduler API
- Sensitive connection details are stored in the database service, not duplicated here
- Query text is stored in plaintext - use parameterized queries for sensitive data

## Troubleshooting

### Job Not Executing

1. Check if job is active: `GET /jobs/{id}`
2. Verify `next_execution_time` is in the past
3. Check worker logs for errors
4. Verify database service is running on port 8002

### Execution Failures

1. Get execution details: `GET /executions/{id}`
2. Check logs: `GET /executions/{id}/logs`
3. Verify connection exists and is valid in database service
4. Test query manually via database service

### Worker Not Running

1. Check service health: `GET /`
2. Verify `worker_active: true` in response
3. Check service logs for startup errors
4. Ensure SCHEDULER_CHECK_INTERVAL is set

## Future Enhancements

- [ ] Cron expression parsing with `croniter` library
- [ ] Email notifications on job failure
- [ ] Slack/Teams integration for alerts
- [ ] Result storage to file system (CSV, Parquet)
- [ ] Job dependencies (run job B after job A succeeds)
- [ ] Job chains and workflows
- [ ] Execution time predictions based on history
- [ ] Resource usage tracking (CPU, memory per job)
- [ ] Frontend Pipelines UI with job wizard

## API Documentation

Interactive API docs available at:
- Swagger UI: `http://localhost:8004/docs`
- ReDoc: `http://localhost:8004/redoc`
