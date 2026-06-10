from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, AsyncGenerator, Dict, List, Optional

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

from shared.audit_log import audit_event
from shared.service_factory import create_service

from .kafka_client import KafkaUnavailableError, kafka_producer

logger = logging.getLogger("aura.ingestion")


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    # Tolerant start: a broker outage degrades publishes, never boot.
    await kafka_producer.start()
    yield
    await kafka_producer.stop()


# Initialize the service using AURA's factory
app = create_service(
    name="Ingestion Gateway",
    service_tag="ingestion_service",
    description="Headless API Gateway for Composable ERP integrations.",
    lifespan=_lifespan,
)

from shared.pii_masking import PIIMaskingMiddleware

app.add_middleware(PIIMaskingMiddleware)

# --- Models ---
# Defined in models.py so the ERP adapters can import LedgerEntry without an
# import cycle (main -> adapters -> models; adapters never import main).
from .models import IngestionPayload, LedgerEntry, RawIngestionPayload  # noqa: F401

# --- ERC Mapping Logic (Mock/Stub for Phase 1) ---

def map_erc_to_internal_id(erc: str, system_origin: str) -> str:
    """
    Normalizes multi-system primary keys.
    In a full production environment, this would hit a Redis cache or a dedicated mapping DB.
    """
    return f"AURA-NORM-{system_origin}-{erc}"


# --- Event Publishing & Adapters ---

from .erp_adapters.netsuite import NetSuiteAdapter
from .erp_adapters.workday import WorkdayAdapter


async def process_raw_batch_async(payload: RawIngestionPayload, system_origin: str):
    """
    Background task to normalize raw ERP payload and publish to Kafka.
    """
    normalized_entries = []

    for raw_entry in payload.entries:
        if system_origin == "NetSuite":
            norm_entry = NetSuiteAdapter.normalize(payload.tenant_id, raw_entry)
        elif system_origin == "Workday":
            norm_entry = WorkdayAdapter.normalize(payload.tenant_id, raw_entry)
        else:
            raise ValueError(f"Unknown system origin: {system_origin}")

        normalized_entries.append(norm_entry.model_dump())

    try:
        await kafka_producer.publish_with_retry(
            topic="aura.ledger.ingested",
            payload={"batch_id": payload.batch_id, "entries": normalized_entries},
            partition_key=payload.tenant_id
        )
    except KafkaUnavailableError as exc:
        # The batch was already 202-accepted; the WORM trail is the
        # traceability contract for what happened to it.
        logger.critical(f"Batch {payload.batch_id} NOT published: {exc}")
        audit_event("ingestion_publish_failed", {
            "batch_id": payload.batch_id,
            "system_origin": system_origin,
            "entry_count": len(normalized_entries),
            "error": str(exc),
        })
        return

    audit_event("ingestion_batch_processed", {
        "batch_id": payload.batch_id,
        "system_origin": system_origin,
        "entry_count": len(normalized_entries),
    })


# --- Endpoints ---

@app.post("/api/v1/ingest/netsuite", status_code=202)
async def ingest_netsuite_batch(
    payload: RawIngestionPayload,
    background_tasks: BackgroundTasks,
    request: Request
):
    audit_event("ingestion_batch_received", {
        "batch_id": payload.batch_id,
        "system_origin": "NetSuite",
        "entry_count": len(payload.entries)
    })
    background_tasks.add_task(process_raw_batch_async, payload, "NetSuite")
    return {"status": "accepted", "batch_id": payload.batch_id, "message": "NetSuite batch queued for normalization."}

@app.post("/api/v1/ingest/workday", status_code=202)
async def ingest_workday_batch(
    payload: RawIngestionPayload,
    background_tasks: BackgroundTasks,
    request: Request
):
    audit_event("ingestion_batch_received", {
        "batch_id": payload.batch_id,
        "system_origin": "Workday",
        "entry_count": len(payload.entries)
    })
    background_tasks.add_task(process_raw_batch_async, payload, "Workday")
    return {"status": "accepted", "batch_id": payload.batch_id, "message": "Workday batch queued for normalization."}

@app.get("/api/v1/ingest/erc-map/{erc}")
async def get_erc_mapping(erc: str, system: str):
    """
    Utility endpoint to verify ERC mappings.
    """
    return {
        "erc": erc,
        "system": system,
        "internal_id": map_erc_to_internal_id(erc, system)
    }
