"""
Job execution engine for scheduler service
Handles actual query execution, retry logic, and result storage
"""

import asyncio
import os
import smtplib
import ssl
import traceback
import uuid
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any, Dict, List, Optional

import httpx

from .models import JobExecution, JobStatus, ScheduledJob
from .repository import SchedulerRepository


class NotificationService:
    """Sends failure notifications via email and/or webhooks."""

    def __init__(self):
        # Email config (optional)
        self.smtp_host = os.getenv("SMTP_HOST", "")
        self.smtp_port = int(os.getenv("SMTP_PORT", "587"))
        self.smtp_user = os.getenv("SMTP_USER", "")
        self.smtp_password = os.getenv("SMTP_PASSWORD", "")
        self.alert_from = os.getenv("ALERT_FROM_EMAIL", self.smtp_user)
        self.alert_to: List[str] = [
            e.strip() for e in os.getenv("ALERT_TO_EMAILS", "").split(",") if e.strip()
        ]
        # Slack / generic webhook (optional)
        self.slack_webhook_url = os.getenv("SLACK_WEBHOOK_URL", "")
        self.generic_webhook_url = os.getenv("ALERT_WEBHOOK_URL", "")

    async def notify_job_failure(
        self,
        job_name: str,
        job_id: str,
        execution_id: str,
        error_message: str,
        retry_count: int,
        max_retries: int,
    ) -> None:
        """Send failure notifications via all configured channels."""
        subject = f"[AURA] Scheduled job failed: {job_name}"
        body = (
            f"Job '{job_name}' (ID: {job_id}) has failed.\n\n"
            f"Execution ID : {execution_id}\n"
            f"Retries      : {retry_count}/{max_retries}\n"
            f"Error        : {error_message}\n"
            f"Time (UTC)   : {datetime.now(timezone.utc).isoformat()}\n"
        )

        tasks = []
        if self.smtp_host and self.alert_to:
            tasks.append(self._send_email(subject, body))
        if self.slack_webhook_url:
            tasks.append(self._send_slack(job_name, error_message, execution_id))
        if self.generic_webhook_url:
            tasks.append(
                self._send_webhook(
                    self.generic_webhook_url,
                    {
                        "event": "job_failed",
                        "job_id": job_id,
                        "job_name": job_name,
                        "execution_id": execution_id,
                        "error": error_message,
                        "retry_count": retry_count,
                        "max_retries": max_retries,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    },
                )
            )

        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for r in results:
                if isinstance(r, Exception):
                    # Log but never crash the scheduler
                    print(f"[NotificationService] Warning: notification delivery failed: {r}")

    async def _send_email(self, subject: str, body: str) -> None:
        """Send an email alert using SMTP."""
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._send_email_sync, subject, body)

    def _send_email_sync(self, subject: str, body: str) -> None:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = self.alert_from
        msg["To"] = ", ".join(self.alert_to)
        msg.attach(MIMEText(body, "plain"))

        context = ssl.create_default_context()
        with smtplib.SMTP(self.smtp_host, self.smtp_port) as server:
            server.ehlo()
            server.starttls(context=context)
            if self.smtp_user and self.smtp_password:
                server.login(self.smtp_user, self.smtp_password)
            server.sendmail(self.alert_from, self.alert_to, msg.as_string())

    async def _send_slack(self, job_name: str, error: str, execution_id: str) -> None:
        """Post a failure message to a Slack incoming webhook."""
        payload = {
            "text": f":red_circle: *AURA Job Failed*: `{job_name}`",
            "attachments": [
                {
                    "color": "danger",
                    "fields": [
                        {"title": "Error", "value": error[:500], "short": False},
                        {"title": "Execution ID", "value": execution_id, "short": True},
                        {"title": "Time (UTC)", "value": datetime.now(timezone.utc).isoformat(), "short": True},
                    ],
                }
            ],
        }
        await self._send_webhook(self.slack_webhook_url, payload)

    @staticmethod
    async def _send_webhook(url: str, payload: Dict[str, Any]) -> None:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(url, json=payload)
            response.raise_for_status()


class JobExecutor:
    """Executes scheduled jobs and handles retries"""

    def __init__(
        self,
        repository: SchedulerRepository,
        database_service_url: str = "http://localhost:8002"
    ):
        self.repository = repository
        self.database_service_url = database_service_url
        self.http_client = httpx.AsyncClient(timeout=300.0)
        self.notifications = NotificationService()

    async def execute_job(
        self,
        job: ScheduledJob,
        triggered_by: str = "scheduler"
    ) -> JobExecution:
        """Execute a single job"""

        # Create execution record
        execution = await self.repository.create_execution({
            "job_id": job.id,
            "status": JobStatus.PENDING,
            "triggered_by": triggered_by,
            "retry_count": 0
        })

        await self._log(execution.id, "INFO", f"Starting execution of job '{job.name}'")

        try:
            # Update status to running
            await self.repository.update_execution(
                execution.id,
                {
                    "status": JobStatus.RUNNING,
                    "started_at": datetime.now(timezone.utc)
                }
            )

            # Execute query via database service
            result = await self._execute_query(
                connection_id=job.connection_id,
                query=job.query,
                timeout=job.timeout_seconds
            )

            # Calculate duration
            duration = (datetime.now(timezone.utc) - execution.started_at).total_seconds() if execution.started_at else 0

            # Update execution with results
            await self.repository.update_execution(
                execution.id,
                {
                    "status": JobStatus.SUCCESS,
                    "completed_at": datetime.now(timezone.utc),
                    "duration_seconds": int(duration),
                    "rows_affected": result.get("row_count", 0),
                    "result_summary": {
                        "columns": result.get("columns", []),
                        "sample_rows": result.get("rows", [])[:5]  # Store first 5 rows
                    }
                }
            )

            await self._log(
                execution.id,
                "INFO",
                f"Job completed successfully. Rows: {result.get('row_count', 0)}, Duration: {duration:.2f}s"
            )

            # Update job's last execution time
            await self.repository.update_job(
                job.id,
                {
                    "last_execution_time": datetime.now(timezone.utc),
                    "next_execution_time": self._calculate_next_execution(job)
                }
            )

        except Exception as e:
            error_message = str(e)
            error_details = {
                "type": type(e).__name__,
                "traceback": traceback.format_exc()
            }

            await self._log(
                execution.id,
                "ERROR",
                f"Job execution failed: {error_message}",
                error_details
            )

            # Check if we should retry
            execution = await self.repository.get_execution(execution.id)
            if execution and execution.retry_count < job.max_retries:
                # Schedule retry
                retry_count = execution.retry_count + 1
                await self.repository.update_execution(
                    execution.id,
                    {
                        "status": JobStatus.PENDING,
                        "retry_count": retry_count,
                        "error_message": error_message,
                        "error_details": error_details
                    }
                )

                await self._log(
                    execution.id,
                    "INFO",
                    f"Scheduling retry {retry_count}/{job.max_retries} in {job.retry_delay_seconds}s"
                )

                # Retry after delay
                await asyncio.sleep(job.retry_delay_seconds)
                return await self.execute_job(job, triggered_by)
            else:
                # Max retries exceeded
                duration = (datetime.now(timezone.utc) - execution.started_at).total_seconds() if execution.started_at else 0

                await self.repository.update_execution(
                    execution.id,
                    {
                        "status": JobStatus.FAILED,
                        "completed_at": datetime.now(timezone.utc),
                        "duration_seconds": int(duration),
                        "error_message": error_message,
                        "error_details": error_details
                    }
                )

                # Send failure notifications (email, Slack, webhook)
                try:
                    await self.notifications.notify_job_failure(
                        job_name=job.name,
                        job_id=job.id,
                        execution_id=execution.id,
                        error_message=error_message,
                        retry_count=execution.retry_count,
                        max_retries=job.max_retries,
                    )
                except Exception as notify_err:
                    await self._log(execution.id, "WARNING", f"Notification failed: {notify_err}")

        # Fetch and return final execution state
        return await self.repository.get_execution(execution.id)

    async def _execute_query(
        self,
        connection_id: str,
        query: str,
        timeout: int = 300
    ) -> Dict[str, Any]:
        """Execute query via database service"""

        url = f"{self.database_service_url}/connections/{connection_id}/query"

        try:
            response = await self.http_client.post(
                url,
                json={
                    "connection_id": connection_id,
                    "query": query,
                    "limit": 10000  # Max rows to fetch
                },
                timeout=timeout
            )
            response.raise_for_status()
            return response.json()

        except httpx.HTTPStatusError as e:
            raise Exception(f"Database service returned error: {e.response.status_code} - {e.response.text}")
        except httpx.TimeoutException:
            raise Exception(f"Query execution timed out after {timeout}s")
        except Exception as e:
            raise Exception(f"Failed to execute query: {str(e)}")

    async def _log(
        self,
        execution_id: str,
        level: str,
        message: str,
        details: Optional[Dict[str, Any]] = None
    ):
        """Add a log entry"""
        await self.repository.add_log({
            "execution_id": execution_id,
            "level": level,
            "message": message,
            "details": details
        })

    def _calculate_next_execution(self, job: ScheduledJob) -> Optional[datetime]:
        """Calculate next execution time based on schedule"""
        now = datetime.now(timezone.utc)

        if not job.is_active:
            return None

        if job.schedule_type == "once":
            return None  # One-time job, no next execution

        elif job.schedule_type == "hourly":
            return now + timedelta(hours=1)

        elif job.schedule_type == "daily":
            # Execute at the same hour tomorrow
            config = job.schedule_config or {}
            hour = config.get("hour", 0)
            minute = config.get("minute", 0)

            next_run = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if next_run <= now:
                next_run += timedelta(days=1)
            return next_run

        elif job.schedule_type == "weekly":
            # Execute on specific day of week
            config = job.schedule_config or {}
            day_of_week = config.get("day_of_week", 0)  # 0=Monday, 6=Sunday
            hour = config.get("hour", 0)
            minute = config.get("minute", 0)

            next_run = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            days_ahead = day_of_week - now.weekday()
            if days_ahead <= 0:
                days_ahead += 7
            next_run += timedelta(days=days_ahead)
            return next_run

        elif job.schedule_type == "monthly":
            # Execute on specific day of month
            config = job.schedule_config or {}
            day = config.get("day", 1)
            hour = config.get("hour", 0)
            minute = config.get("minute", 0)

            next_run = now.replace(day=day, hour=hour, minute=minute, second=0, microsecond=0)
            if next_run <= now:
                # Move to next month
                if next_run.month == 12:
                    next_run = next_run.replace(year=next_run.year + 1, month=1)
                else:
                    next_run = next_run.replace(month=next_run.month + 1)
            return next_run

        # For cron expressions, would need a cron parser library
        # For now, default to hourly
        return now + timedelta(hours=1)

    async def close(self):
        """Cleanup resources"""
        await self.http_client.aclose()
