"""
UASR Persistence Layer
========================
Initializes and provides async database access for all UASR tables.
Reuses the shared metadata store engine so everything lives in one DB.
"""
from __future__ import annotations

from metadata_store.db import Base, get_engine, get_session  # type: ignore

# Importing the models ensures SQLAlchemy registers the table metadata
from .models import (  # noqa: F401
    DriftEvent,
    RecoveryRecord,
    DistributionSnapshot,
    BatchEmbeddingRecord,
    HealingMetric,
)


async def init_uasr_db() -> None:
    """Create all UASR tables if they don't exist."""
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


__all__ = ["init_uasr_db", "get_session"]
