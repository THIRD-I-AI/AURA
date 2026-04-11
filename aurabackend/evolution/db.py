"""Evolution DB bootstrap — creates all evolution tables."""
from __future__ import annotations

from metadata_store.db import Base, get_engine

from .models import (  # noqa: F401 — registers table metadata
    AgentFeedback,
    ExecutionPattern,
    ImprovementProposal,
    SystemEvolutionLog,
)


async def init_evolution_db() -> None:
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
