import asyncio
import logging
from typing import Any, Dict

import httpx

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("aura.synthetic_monitor")

BASE_URL = "http://localhost:8000/api/v1/ingest"

async def ping_endpoint(client: httpx.AsyncClient, endpoint: str, payload: Dict[str, Any], expected_status: int):
    """
    Pings an ingestion endpoint and verifies the status code.
    """
    url = f"{BASE_URL}/{endpoint}"
    try:
        response = await client.post(url, json=payload, timeout=5.0)
        if response.status_code == expected_status:
            logger.info(f"[SUCCESS] {endpoint} returned expected status {expected_status}")
        else:
            logger.error(f"[FAILURE] {endpoint} returned {response.status_code}, expected {expected_status}. Response: {response.text}")
    except httpx.RequestError as e:
        logger.error(f"[FAILURE] Connection error to {endpoint}: {e}")

async def run_monitor():
    """
    Runs synthetic tests against the API Gateway.
    In production, this runs via cron every 5 minutes to ensure ERP API stability.
    """
    logger.info("Starting Synthetic Monitoring Run...")

    valid_netsuite_payload = {
        "batch_id": "synth-batch-ns-001",
        "tenant_id": "tenant-monitor",
        "entries": [
            {
                "internalId": "synth-100",
                "tranType": "Journal",
                "debit": 100.0,
                "credit": 0.0,
                "tranDate": "2026-06-09T12:00:00Z"
            }
        ]
    }

    # Missing critical fields to trigger Pydantic validation failures
    invalid_netsuite_payload = {
        "batch_id": "synth-batch-ns-002",
        "entries": [
            {
                "tranType": "Journal"
            }
        ]
    }

    async with httpx.AsyncClient() as client:
        # Test 1: Valid NetSuite Payload -> Expect 202 Accepted
        await ping_endpoint(client, "netsuite", valid_netsuite_payload, 202)

        # Test 2: Invalid NetSuite Payload -> Expect 422 Unprocessable Entity
        await ping_endpoint(client, "netsuite", invalid_netsuite_payload, 422)

    logger.info("Synthetic Monitoring Run Complete.")

if __name__ == "__main__":
    asyncio.run(run_monitor())
