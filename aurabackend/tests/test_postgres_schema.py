"""
Tier-B Postgres schema smoke-test (S43).

Proves that every SQLAlchemy store's schema builds (create_all) and tears
down (drop_all) cleanly on Postgres.  Skipped automatically when the
AURA_PG_TEST_DSN environment variable is not set, so the standard CI
lane (SQLite) stays unaffected.

Run manually:
    AURA_PG_TEST_DSN="postgresql+asyncpg://postgres:aura@localhost:55432/aura" \\
        ../.venv/Scripts/python.exe -m pytest tests/test_postgres_schema.py -q
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # noqa: E402

import pytest  # noqa: E402

pytestmark = pytest.mark.skipif(
    not os.getenv("AURA_PG_TEST_DSN"),
    reason="needs Postgres (set AURA_PG_TEST_DSN=postgresql+asyncpg://...)",
)


async def _build_and_drop(base):
    """Create then immediately drop all tables registered on *base*."""
    from sqlalchemy.ext.asyncio import create_async_engine

    eng = create_async_engine(os.environ["AURA_PG_TEST_DSN"])
    try:
        async with eng.begin() as conn:
            await conn.run_sync(base.metadata.create_all)
            await conn.run_sync(base.metadata.drop_all)
    finally:
        await eng.dispose()


@pytest.mark.asyncio
async def test_metadata_store_schema_builds_on_postgres():
    """metadata_store.db.Base — User, DataSource, Document, DocumentEmbedding,
    SchemaColumn, DARInsight, DatasetProfile, SemanticModel, SemanticField,
    plus all UASR tables that register on this same Base (uasr/models.py)."""
    # Import models so their tables register on Base.metadata before
    # create_all is called.  Mirror what the app's init_db() does.
    import metadata_store.models  # noqa: F401
    import uasr.models  # noqa: F401
    from metadata_store.db import Base

    await _build_and_drop(Base)


@pytest.mark.asyncio
async def test_gateway_persistence_schema_builds_on_postgres():
    """api_gateway.persistence.Base — QueryHistoryRow, SavedQueryRow,
    SchemaContextRow, FileMetadataRow, ShareTokenRow, LineageEdgeRow."""
    from api_gateway.persistence import Base

    await _build_and_drop(Base)


@pytest.mark.asyncio
async def test_scheduler_schema_builds_on_postgres():
    """scheduler_service.models.Base — ScheduledJob, JobExecution,
    ExecutionLog."""
    # Import models to ensure tables register on this Base.
    import scheduler_service.models  # noqa: F401
    from scheduler_service.models import Base

    await _build_and_drop(Base)
