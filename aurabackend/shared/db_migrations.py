"""
Alembic migration runner
=========================
Small async wrapper so the API gateway lifespan can ``await`` an
``alembic upgrade head`` without shelling out. Alembic itself is sync,
so the actual work runs in a thread via ``asyncio.to_thread``.

The Alembic config lives at ``aurabackend/alembic.ini`` relative to
this file; env.py pulls the DB URL from ``metadata_store.db.DATABASE_URL``.
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

from alembic.config import Config

from alembic import command

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
_ALEMBIC_INI = _BACKEND_ROOT / "alembic.ini"

# Hard cap on migration time. SQLite hangs forever on file locks
# (e.g. a stale process holding metadata.db open) — without a timeout
# the entire API gateway lifespan blocks silently. The lifespan caller
# treats a TimeoutError like any other migration failure and falls
# back to create_all, so a missed migration is at least visible.
_MIGRATION_TIMEOUT_SECONDS = float(os.getenv("AURA_MIGRATION_TIMEOUT", "30"))


def _upgrade_head_sync() -> None:
    cfg = Config(str(_ALEMBIC_INI))
    # Ensure script_location resolves relative to aurabackend/ regardless
    # of the process's current working directory.
    cfg.set_main_option("script_location", str(_BACKEND_ROOT / "alembic"))
    command.upgrade(cfg, "head")


async def run_migrations_to_head() -> None:
    """Run ``alembic upgrade head`` in a worker thread, bounded by AURA_MIGRATION_TIMEOUT."""
    await asyncio.wait_for(
        asyncio.to_thread(_upgrade_head_sync),
        timeout=_MIGRATION_TIMEOUT_SECONDS,
    )
