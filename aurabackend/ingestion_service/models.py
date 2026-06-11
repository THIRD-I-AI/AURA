"""Shared ingestion models.

Kept separate from ``main`` so the ERP adapters can import ``LedgerEntry``
without creating an import cycle (``main`` imports the adapters, the adapters
import the models — adapters must NOT reach back into ``main``).
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List

from pydantic import BaseModel, Field


class LedgerEntry(BaseModel):
    erc: str = Field(..., description="External Reference Code for mapping across ERPs")
    system_origin: str = Field(..., description="The source ERP system (e.g., 'SAP_EU', 'Oracle_NA')")
    amount: float
    currency: str
    account_code: str
    posted_at: datetime
    metadata: Dict[str, Any] = Field(default_factory=dict)


class IngestionPayload(BaseModel):
    batch_id: str
    entries: List[LedgerEntry]


class RawIngestionPayload(BaseModel):
    batch_id: str
    tenant_id: str
    entries: List[Dict[str, Any]]
