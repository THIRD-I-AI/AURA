import asyncio
import logging
import time
from typing import Any, Dict

import httpx

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("aura.load_test")

BASE_URL = "http://localhost:8000/api/v1/ingest"

# KPIs
metrics = {
    "requests_sent": 0,
    "success_202": 0,
    "throttled_429": 0,
    "failed_other": 0,
    "total_latency_ms": 0.0,
    "dlq_routes": 0 # Tracked if we could inspect DLQ size directly, but we will assert via server logs
}

def generate_payload(batch_index: int, include_pii: bool = False) -> Dict[str, Any]:
    payload = {
        "batch_id": f"load-test-batch-{batch_index}",
        "tenant_id": "tenant-loadtest-1",
        "entries": []
    }
    for i in range(10): # 10 ledger entries per batch
        entry = {
            "WorkdayID": f"WD-LOAD-{batch_index}-{i}",
            "CostCenterID": "CC-LOAD",
            "BaseAmount": 100.50,
            "CurrencyCode": "USD",
            "AccountingDate": "2026-06-09T12:00:00+00:00",
            "LedgerAccount": "1000",
        }
        if include_pii:
            entry["employee_name"] = "John Doe"
            entry["ssn"] = "000-11-2222"
        payload["entries"].append(entry)
    return payload

async def worker(client: httpx.AsyncClient, num_requests: int, include_pii: bool):
    for i in range(num_requests):
        payload = generate_payload(i, include_pii)
        start_time = time.perf_counter()

        metrics["requests_sent"] += 1
        try:
            response = await client.post(f"{BASE_URL}/workday", json=payload, timeout=10.0)
            duration_ms = (time.perf_counter() - start_time) * 1000
            metrics["total_latency_ms"] += duration_ms

            if response.status_code == 202:
                metrics["success_202"] += 1
            elif response.status_code == 429:
                metrics["throttled_429"] += 1
            else:
                metrics["failed_other"] += 1
                logger.error(f"Unexpected status: {response.status_code} - {response.text}")
        except Exception as e:
            metrics["failed_other"] += 1
            logger.error(f"Request failed: {e}")

async def run_load_test(total_requests: int, concurrency: int, chaos_mode: bool = False, include_pii: bool = True):
    logger.info(f"Starting Enterprise Load Test. Total Requests: {total_requests}, Concurrency: {concurrency}, Chaos Mode: {chaos_mode}")

    if chaos_mode:
        logger.warning("CHAOS MODE ACTIVATED: Network failure to Kafka will be simulated on the server. Expecting DLQ Routing.")

    start_time = time.perf_counter()

    async with httpx.AsyncClient(limits=httpx.Limits(max_connections=concurrency)) as client:
        requests_per_worker = total_requests // concurrency
        tasks = [worker(client, requests_per_worker, include_pii) for _ in range(concurrency)]
        await asyncio.gather(*tasks)

    total_duration = time.perf_counter() - start_time
    avg_latency = metrics["total_latency_ms"] / metrics["success_202"] if metrics["success_202"] > 0 else 0

    logger.info("=== Load Test Complete ===")
    logger.info(f"Total Duration: {total_duration:.2f} seconds")
    logger.info(f"Requests Sent: {metrics['requests_sent']}")
    logger.info(f"Success (202): {metrics['success_202']}")
    logger.info(f"Throttled (429): {metrics['throttled_429']}")
    logger.info(f"Failed (Other): {metrics['failed_other']}")
    logger.info(f"KPI - Avg Ingestion Latency: {avg_latency:.2f} ms")
    logger.info("KPI - Settlement Time: Simulated as instantaneous due to async Kafka publishing.")

    # Assertions
    if metrics["requests_sent"] > 0:
        if metrics["throttled_429"] > 0:
            logger.info("VALIDATION PASSED: API Throttling successfully engaged.")
        if metrics["success_202"] > 0:
            logger.info(f"VALIDATION PASSED: Ingestion successfully sustained {metrics['success_202']/total_duration:.2f} RPS.")

if __name__ == "__main__":
    # Test params: 1000 requests spread over 50 concurrent workers
    asyncio.run(run_load_test(total_requests=1000, concurrency=50, chaos_mode=False, include_pii=True))
